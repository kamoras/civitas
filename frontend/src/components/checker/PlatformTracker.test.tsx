import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PlatformTracker from "./PlatformTracker";
import type { PartisanDepth } from "@/types/senator";

vi.mock("@/hooks/useConfig", () => ({
  usePolicyLabel: (area: string) => area,
}));

function depth(overallLean: number): PartisanDepth {
  return {
    overallLean,
    overallParty: overallLean > 0 ? "R" : "D",
    depth: "deep",
    crossPartyCount: 0,
    totalPositions: 12,
    policyBreakdown: [],
  };
}

async function spectrum(overallLean: number) {
  const { container } = render(
    <PlatformTracker partisanDepth={depth(overallLean)} senatorParty="R" />
  );
  await userEvent.click(screen.getByRole("button", { name: /POSITIONS vs\. VOTES/ }));
  const marker = container.querySelector<HTMLElement>(".w-1.bg-phos");
  const fill = container.querySelector<HTMLElement>(".bg-signal-red, .bg-dem-blue");
  return { marker: marker!.style.left, fill: fill!.style.width };
}

describe("PlatformTracker spectrum bar", () => {
  // overallLean is a mean of (R - D) / partisan votes, bounded by +/-1, so
  // the bar's ends are +/-1: a measured lean draws in proportion, not
  // pinned at an end as the old /0.15 scale drew most members.
  it("draws a lean on its own -1..+1 scale", async () => {
    const { marker, fill } = await spectrum(0.43);
    expect(fill).toBe("21.5%");
    expect(marker).toContain("71.5%");
  });

  it("draws a Democratic lean left of center", async () => {
    const { marker, fill } = await spectrum(-0.3);
    expect(fill).toBe("15%");
    expect(marker).toContain("35%");
  });

  // A member with few votes is blended toward an extrapolated cosponsorship
  // prior, which could pass +/-1: drawn at the end, never past it.
  it("draws a lean past -1 at the end", async () => {
    const { marker, fill } = await spectrum(-1.5);
    expect(fill).toBe("50%");
    expect(marker).toMatch(/^clamp\(2px, -25%, /);
  });

  it("keeps the marker inside the track at an end", async () => {
    const { marker } = await spectrum(1);
    expect(marker).toMatch(/^clamp\(2px, 100%, /);
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
    await userEvent.click(screen.getByRole("button", { name: /POSITIONS vs\. VOTES/ }));
    expect(screen.getByText("Voting record sits at the center overall.")).toBeInTheDocument();
    expect(screen.queryByText(/voting record leans/)).not.toBeInTheDocument();
  });
});
