import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PlatformTracker from "./PlatformTracker";
import type { PartisanDepth } from "@/types/senator";

vi.mock("@/hooks/useConfig", () => ({
  usePolicyLabel: (area: string) => area,
}));

// What the API sends for a lean (PartisanDepthSchema's computed fields).
function depth(overallLean: number, place = { marker: 50, left: 50, width: 0 }): PartisanDepth {
  return {
    overallLean,
    overallParty: overallLean > 0 ? "R" : "D",
    depth: "deep",
    crossPartyCount: 0,
    totalPositions: 12,
    policyBreakdown: [
      {
        area: "foreign_policy",
        alignment: "R",
        strength: 0.63,
        strengthShown: "63%",
        barPercent: 63,
        mark: {
          id: "foreign_policy",
          label: "Foreign Policy",
          shown: "leans Republican",
          context: "strength 63%",
        },
      },
    ],
    leanShown: "+0.43",
    leanSentence: "Votes lean Republican: +0.43, on a scale from \u22121 to +1.",
    leanMarkerPercent: place.marker,
    leanBarLeft: place.left,
    leanBarWidth: place.width,
  };
}

async function open(d: PartisanDepth) {
  const view = render(<PlatformTracker partisanDepth={d} senatorParty="R" />);
  await userEvent.click(screen.getByRole("button", { name: /Positions vs\. Votes/ }));
  return view;
}

describe("PlatformTracker spectrum bar", () => {
  it("draws the lean where the API places it, and says it in words", async () => {
    const { container } = await open(depth(0.43, { marker: 71.5, left: 50, width: 21.5 }));
    expect(
      screen.getByRole("img", { name: /^Votes lean Republican: \+0\.43/ })
    ).toBeInTheDocument();
    const fill = container.querySelector<HTMLElement>(".bg-signal-red, .bg-dem-blue")!;
    expect([fill.style.left, fill.style.width]).toEqual(["50%", "21.5%"]);
    expect(container.querySelector<HTMLElement>(".w-1.bg-phos")!.style.left).toBe(
      "clamp(2px, 71.5%, 100% - 2px)"
    );
  });

  it("reads each policy area, with a table of the same figures", async () => {
    await open(depth(0.43));
    const chart = screen.getByRole("group", { name: /^Voting record by policy area: 1 mark/ });
    chart.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByText("Foreign Policy: leans Republican, strength 63%.")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "63%" })).toBeInTheDocument();
  });
});

describe("PlatformTracker interpretation", () => {
  it("calls a lean at the center centrist, not the other party's", async () => {
    render(
      <PlatformTracker
        partisanDepth={{ ...depth(0.01), overallParty: "centrist", depth: "centrist" }}
        senatorParty="R"
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /Positions vs\. Votes/ }));
    expect(screen.getByText("Voting record sits at the center overall.")).toBeInTheDocument();
    expect(screen.queryByText(/voting record leans/)).not.toBeInTheDocument();
  });
});
