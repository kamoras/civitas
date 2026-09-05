import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StateBallotClient from "./StateBallotClient";
import type { RaceWithCandidates, StateBallot } from "@/types/election";

vi.mock("@/lib/api", () => ({
  fetchTownsForState: vi.fn().mockResolvedValue([]),
  fetchTownBallot: vi.fn(),
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

// jsdom doesn't implement scrollIntoView — the app code's real, correct
// call to it (deep-linking/expand-to-district) just has nothing to call
// in this environment.
Element.prototype.scrollIntoView = vi.fn();
vi.mock("@/components/elections/CoverageFeed", async () => {
  const actual = await vi.importActual<typeof import("@/components/elections/CoverageFeed")>(
    "@/components/elections/CoverageFeed"
  );
  return { ...actual, default: () => <div /> };
});

function candidate(overrides: Partial<RaceWithCandidates["candidates"][number]>) {
  return {
    id: "c1",
    name: "Jane Doe",
    party: "DEM",
    incumbentChallenge: null as string | null,
    candidateStatus: "C" as string | null,
    hasRaisedFunds: true,
    contributions: null as number | null,
    cashOnHand: null as number | null,
    lastFinancialsSync: null as string | null,
    incumbentRecord: null,
    ...overrides,
  };
}

function houseRace(overrides: Partial<RaceWithCandidates> = {}): RaceWithCandidates {
  return {
    id: "2026-HOUSE-OH-1",
    cycleYear: 2026,
    office: "H",
    state: "OH",
    district: 1,
    isSpecial: false,
    pvi: -3,
    pviLevel: "district",
    candidateSource: "filers",
    counties: ["Hamilton County (part)"],
    candidates: [
      candidate({ id: "dem1", name: "Greg Landsman", party: "DEM", incumbentChallenge: "I", cashOnHand: 3_610_213 }),
      candidate({ id: "rep1", name: "Eric Conroy", party: "REP", cashOnHand: 474_156 }),
    ],
    ...overrides,
  };
}

function ballot(overrides: Partial<StateBallot> = {}): StateBallot {
  return {
    state: "OH",
    cycleYear: 2026,
    electionDate: "2026-11-03",
    electionType: "general",
    primaryDate: null,
    statePvi: 6,
    senateRaces: [],
    nextSenateElection: null,
    houseRaces: [houseRace()],
    coverage: [],
    measures: [],
    measureCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
    officialLookup: {
      url: "https://www.usa.gov/election-office",
      label: "Find your election office",
      sourceName: "USA.gov",
      isStateSpecific: false,
      verifiedAt: null,
    },
    omits: [],
    ...overrides,
  };
}

describe("StateBallotClient — House section", () => {
  it("shows every district's leading matchup without any selection", () => {
    render(<StateBallotClient ballot={ballot()} />);

    // The whole point: no dropdown, no address entry needed to see this.
    expect(screen.getByText("Greg Landsman (I)")).toBeInTheDocument();
    expect(screen.getByText("Eric Conroy")).toBeInTheDocument();
    expect(screen.queryByLabelText("Select your district")).not.toBeInTheDocument();
  });

  it("expands a district's full detail on click", async () => {
    render(<StateBallotClient ballot={ballot()} />);

    expect(screen.queryByText(/News coverage of this race is tagged/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Greg Landsman (I)"));
    expect(screen.getByText(/News coverage of this race is tagged/)).toBeInTheDocument();
  });

  it("shows multiple districts, each independently collapsed", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          houseRaces: [
            houseRace({ id: "d1", district: 1 }),
            houseRace({
              id: "d2",
              district: 2,
              candidates: [
                candidate({ id: "dem2", name: "Second District Dem", party: "DEM", cashOnHand: 1000 }),
                candidate({ id: "rep2", name: "Second District Rep", party: "REP", cashOnHand: 900 }),
              ],
            }),
          ],
        })}
      />
    );

    expect(screen.getByText("Greg Landsman (I)")).toBeInTheDocument();
    expect(screen.getByText("Second District Dem")).toBeInTheDocument();
    expect(screen.getByText("U.S. HOUSE — 2 DISTRICTS")).toBeInTheDocument();
  });
});
