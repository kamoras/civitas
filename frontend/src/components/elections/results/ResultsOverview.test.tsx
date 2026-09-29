import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import ResultsOverview from "./ResultsOverview";
import type { LiveRaceResult, LiveResults } from "@/types/election";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/elections/RaceMap", () => ({ default: () => null }));

afterEach(cleanup);

function race(overrides: Partial<LiveRaceResult> = {}): LiveRaceResult {
  return {
    raceId: "2026-HOUSE-GA-2",
    state: "GA",
    office: "H",
    district: 2,
    isSpecial: false,
    heldBy: "DEM",
    official: false,
    votesCounted: 1900,
    reportingUnits: 80,
    totalUnits: 100,
    unitLabel: "precincts",
    sourceName: "GA SOS",
    sourceUrl: null,
    fetchedAt: "2026-11-04T02:44:00Z",
    lastChangeAt: "2026-11-04T02:42:00Z",
    leaderParty: "REP",
    flip: true,
    candidates: [
      { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
    ],
    ...overrides,
  };
}

function results(overrides: Partial<LiveResults> = {}): LiveResults {
  return {
    cycleYear: 2026,
    phase: {
      phase: "results",
      electionDate: "2026-11-03",
      resultsUntil: "2026-11-20",
      lastResultChange: "2026-11-04T02:42:00Z",
    },
    liveStates: ["GA", "TX", "NC"],
    senateStates: [],
    pollsClose: {},
    races: [race()],
    updates: [],
    ...overrides,
  } as LiveResults;
}

function flipsCard() {
  return screen.getByText("SEATS CHANGING PARTY").parentElement as HTMLElement;
}

describe("the seats-changing-party card", () => {
  it("says House seats on new lines are not counted, naming the states", () => {
    render(
      <ResultsOverview
        results={results({
          redrawnStates: ["TX", "NC"],
          races: [
            race(),
            race({
              raceId: "2026-HOUSE-TX-35",
              state: "TX",
              district: 35,
              heldBy: null,
              flip: false,
            }),
            race({
              raceId: "2026-HOUSE-TX-33",
              state: "TX",
              district: 33,
              heldBy: null,
              flip: false,
            }),
            race({
              raceId: "2026-HOUSE-NC-1",
              state: "NC",
              district: 1,
              heldBy: null,
              flip: false,
              votesCounted: 0,
            }),
          ],
        })}
        states={["GA", "NC", "TX"]}
      />
    );
    const card = within(flipsCard());
    expect(card.getByText("1")).toBeInTheDocument();
    expect(
      card.getByText(
        "Not counted: House seats in the 2 states voting on new district lines (NC, TX), which have no previous holder — 2 of the districts with a count so far are among them."
      )
    ).toBeInTheDocument();
  });

  it("adds no qualifier when no state redrew, or for an older backend", () => {
    render(<ResultsOverview results={results()} states={["GA"]} />);
    expect(within(flipsCard()).queryByText(/Not counted/)).not.toBeInTheDocument();
    cleanup();
    render(<ResultsOverview results={results({ redrawnStates: [] })} states={["GA"]} />);
    expect(within(flipsCard()).queryByText(/Not counted/)).not.toBeInTheDocument();
  });

  it("names one state in the singular, before any count", () => {
    render(
      <ResultsOverview results={results({ redrawnStates: ["UT"], races: [] })} states={["UT"]} />
    );
    expect(
      within(flipsCard()).getByText(
        "Not counted: House seats in the state voting on new district lines (UT), which have no previous holder."
      )
    ).toBeInTheDocument();
  });
});

describe("the national results map's key", () => {
  it("calls a count under half in fainter, not paler: the fill is opacity over a dark map", () => {
    render(<ResultsOverview results={results()} states={["GA"]} />);
    expect(screen.getByText(/FAINTER: UNDER HALF IN/)).toBeInTheDocument();
    expect(screen.getByText(/Fainter means fewer than half/)).toBeInTheDocument();
    expect(screen.queryByText(/paler/i)).not.toBeInTheDocument();
  });
});
