import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComparePage from "./page";
import type { Senator } from "@/types/senator";

/* /compare opened on two empty pickers. Quick comparisons fill both sides
   with no typing, and the pair goes into the address bar through the
   History API: router.replace does nothing on this statically prerendered
   route once it was loaded with a query string (AGENTS.md, "Client-side URL
   state"). */

const routerReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
  useRouter: () => ({ push: vi.fn(), replace: routerReplace }),
}));

function member(id: string, name: string, state: string, party: "D" | "R"): Senator {
  return {
    id,
    name,
    state,
    party,
    representationScore: {
      overall: 60,
      fundingIndependence: 50,
      constituentAlignment: 70,
      legislativeEffectiveness: 40,
      fundingDiversity: 55,
    },
    scoredOn: "2026-10-16",
    algorithmVersion: "v6.35",
    funding: { pacSharePct: 10, totalFromPacs: 1000, totalRaised: 10000 },
  } as unknown as Senator;
}

const NORTH = [member("n-1", "Member One", "NA", "D"), member("n-2", "Member Two", "NA", "R")];

vi.mock("@/lib/api", () => ({
  fetchStates: vi.fn(async () => [
    { code: "NA", name: "North", senatorCount: 2 },
    { code: "SO", name: "South", senatorCount: 1 },
  ]),
  fetchRepStates: vi.fn(async () => []),
  fetchSenatorsByState: vi.fn(async (state: string) => (state === "NA" ? NORTH : [])),
  fetchRepresentativesByState: vi.fn(async () => ({ entries: [] })),
  fetchSenator: vi.fn(async (id: string) => NORTH.find((m) => m.id === id)),
  fetchRepresentative: vi.fn(),
  fetchCompareAgreement: vi.fn(async () => ({
    chamber: "senate",
    congress: 119,
    bothVoted: 10,
    agreed: 4,
    split: 6,
    agreementPct: 40,
    sentence:
      "Voted the same way on 4 of the 10 roll calls this Congress where both cast a Yea or a Nay (40%).",
    recentSplits: [],
  })),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));

beforeEach(() => window.history.replaceState(null, "", "/compare"));
afterEach(() => {
  cleanup();
  routerReplace.mockReset();
});

describe("compare: citable", () => {
  it("names each score's run and method, shows Funding Diversity apart, and cites the pair", async () => {
    window.history.replaceState(
      null,
      "",
      "/compare?leftId=n-1&leftChamber=senate&rightId=n-2&rightChamber=senate"
    );
    render(<ComparePage />);
    expect(await screen.findAllByText("Scored 2026-10-16 · v6.35")).toHaveLength(2);
    expect(screen.getByText("FUNDING DIVERSITY")).toBeInTheDocument();
    expect(screen.getByText("not in the score")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cite this comparison" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /Member One and Member Two: Representation scores compared, scoring method v6\.35/
      )
    ).toBeInTheDocument();
  });
});

describe("compare presets", () => {
  it("pairs both senators of a state and records the pair in the URL", async () => {
    render(<ComparePage />);
    const picker = await screen.findByLabelText("Both senators from");
    // A state with one senator has no pair to offer.
    await within(picker).findByRole("option", { name: "NA · North" });
    expect(within(picker).queryByRole("option", { name: "SO · South" })).not.toBeInTheDocument();

    await userEvent.selectOptions(picker, "NA");

    expect(await screen.findAllByText("Member One")).not.toHaveLength(0);
    expect(window.location.search).toBe(
      "?leftId=n-1&leftChamber=senate&rightId=n-2&rightChamber=senate"
    );
    expect(routerReplace).not.toHaveBeenCalled();
    // The pickers show the pair rather than the empty state they were on.
    const pressed = (name: RegExp) =>
      screen
        .getAllByRole("button", { name })
        .filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(await screen.findAllByRole("button", { name: /Member Two/ })).not.toHaveLength(0);
    expect(pressed(/Member One/)).toHaveLength(1);
    expect(pressed(/Member Two/)).toHaveLength(1);
    // Two senators side by side: how often they voted alike.
    expect(await screen.findByText("40%")).toBeTruthy();
  });

  it("offers a chosen member against their state's other senator", async () => {
    window.history.replaceState(null, "", "/compare?leftId=n-1&leftChamber=senate");
    render(<ComparePage />);

    const line = await screen.findByText(/Member One against the other NA senator/);
    const offer = within(line.parentElement as HTMLElement).getByRole("button", {
      name: /Member Two/,
    });
    await userEvent.click(offer);

    expect(window.location.search).toBe(
      "?leftId=n-1&leftChamber=senate&rightId=n-2&rightChamber=senate"
    );
  });
});
