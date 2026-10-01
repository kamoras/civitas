import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ResultsOverview from "./ResultsOverview";
import type { LiveRaceResult, LiveResults } from "@/types/election";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// The map's props, so a test can read each state's fill and name.
const mapProps = vi.hoisted(() => ({
  current: null as null | {
    getFillColor: (s: string) => string;
    getStateLabel: (s: string) => string;
  },
}));
vi.mock("@/components/elections/RaceMap", () => ({
  default: (props: {
    getFillColor: (s: string) => string;
    getStateLabel: (s: string) => string;
  }) => {
    mapProps.current = props;
    return null;
  },
}));

const NOW = Date.parse("2026-11-04T03:00:00Z");

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
        now={NOW}
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
        "Not counted: House seats in the 2 states voting on new district lines (NC, TX), which have no previous holder; 2 of the districts with a count so far are among them."
      )
    ).toBeInTheDocument();
  });

  it("adds no qualifier when no state redrew, or for an older backend", () => {
    render(<ResultsOverview now={NOW} results={results()} states={["GA"]} />);
    expect(within(flipsCard()).queryByText(/Not counted/)).not.toBeInTheDocument();
    cleanup();
    render(<ResultsOverview now={NOW} results={results({ redrawnStates: [] })} states={["GA"]} />);
    expect(within(flipsCard()).queryByText(/Not counted/)).not.toBeInTheDocument();
  });

  it("names one state in the singular, before any count", () => {
    render(
      <ResultsOverview
        now={NOW}
        results={results({ redrawnStates: ["UT"], races: [] })}
        states={["UT"]}
      />
    );
    expect(
      within(flipsCard()).getByText(
        "Not counted: House seats in the state voting on new district lines (UT), which have no previous holder."
      )
    ).toBeInTheDocument();
  });
});

describe("a flip announced earlier that the latest count doesn't show", () => {
  // A poll whose total fell announces nothing, so the backend keeps the flip
  // (as the issue and feed do) while its figures show the holder ahead.
  const held = race({
    raceId: "2026-HOUSE-GA-3",
    district: 3,
    leaderParty: "DEM",
    candidates: [
      { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
      { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
    ],
  });
  const heldSenate = {
    ...held,
    raceId: "2026-SEN-GA",
    office: "S" as const,
    district: null,
  };

  it("is not a seat changing party in the counter, and the card names it separately", () => {
    render(
      <ResultsOverview now={NOW} results={results({ races: [race(), held] })} states={["GA"]} />
    );
    const card = within(flipsCard());
    expect(card.getByText("1")).toBeInTheDocument();
    expect(
      card.getByText(
        "Not counted: 1 change of party announced earlier that the latest count doesn't show; each race's card says what its count shows"
      )
    ).toBeInTheDocument();
  });

  it("never says 'changing party' or '· flip' beside the holder's lead", () => {
    render(
      <ResultsOverview
        now={NOW}
        results={results({ senateStates: ["GA"], races: [heldSenate] })}
        states={["GA"]}
      />
    );
    expect(within(flipsCard()).getByText("0")).toBeInTheDocument();
    expect(
      screen.getByText("Senate: Dana Smith (D) leads · flip announced, not in latest count")
    ).toBeInTheDocument();
    expect(screen.queryByText(/· flip$/)).not.toBeInTheDocument();
    const label = mapProps.current!.getStateLabel("GA");
    expect(label).toContain("change of party announced earlier, holder's party ahead");
    expect(label).not.toContain("seat changing party");
  });
});

describe("a held Senate flip that the counter lists as not counted", () => {
  const base = { raceId: "2026-SEN-GA", office: "S" as const, district: null };

  it("keeps its note on a tie", () => {
    const tied = race({
      ...base,
      leaderParty: null,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
      ],
    });
    render(
      <ResultsOverview
        now={NOW}
        results={results({ senateStates: ["GA"], races: [tied] })}
        states={["GA"]}
      />
    );
    expect(
      screen.getByText("Senate: tied · flip announced, not in latest count")
    ).toBeInTheDocument();
  });

  it("says no votes are in the latest count, not 'no votes yet'", () => {
    const empty = race({
      ...base,
      votesCounted: 0,
      leaderParty: null,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 0, pct: null, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 0, pct: null, candidateId: null },
      ],
    });
    render(
      <ResultsOverview
        now={NOW}
        results={results({ senateStates: ["GA"], races: [empty] })}
        states={["GA"]}
      />
    );
    expect(
      screen.getByText("Senate: no votes in the latest count · flip announced, not in latest count")
    ).toBeInTheDocument();
    expect(within(flipsCard()).getByText(/Not counted: 1 change of party/)).toBeInTheDocument();
    expect(mapProps.current!.getStateLabel("GA")).toBe(
      "GA Senate: no votes in the latest count, change of party announced earlier"
    );
  });
});

describe("the national results map's key", () => {
  it("calls a count under half in fainter, not paler: the fill is opacity over a dark map", () => {
    render(<ResultsOverview now={NOW} results={results()} states={["GA"]} />);
    expect(screen.getByText(/FAINTER: UNDER HALF IN/)).toBeInTheDocument();
    expect(screen.getByText(/Fainter means fewer than half/)).toBeInTheDocument();
    expect(screen.queryByText(/paler/i)).not.toBeInTheDocument();
  });
});

describe("a Senate race on a directory row", () => {
  it("still says leads in a count the state lists as official — never a bare 'official'", () => {
    render(
      <ResultsOverview
        now={NOW}
        results={results({
          senateStates: ["GA"],
          races: [
            race({
              raceId: "2026-SEN-GA",
              office: "S",
              district: null,
              official: true,
              flip: false,
              leaderParty: "DEM",
              candidates: [
                { name: "Jane Roe", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
                { name: "Sam Poe", party: "REP", votes: 900, pct: 47.4, candidateId: null },
              ],
            }),
          ],
        })}
        states={["GA"]}
      />
    );
    const row = within(screen.getByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(row).toHaveTextContent("Senate: Jane Roe (D) leads · official count");
    expect(row).not.toHaveTextContent(/\(D\) official/);
  });
});

describe("a state electing both senators, one counted", () => {
  const twoSeats = (overrides: Partial<LiveResults> = {}) =>
    results({
      senateStates: ["GA"],
      senateRaces: {
        GA: [
          { raceId: "2026-SEN-GA", isSpecial: false },
          { raceId: "2026-SEN-GA-SPECIAL", isSpecial: true },
        ],
      },
      races: [
        race({
          raceId: "2026-SEN-GA",
          office: "S",
          district: null,
          flip: false,
          leaderParty: "DEM",
          candidates: [
            { name: "Jane Roe", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
            { name: "Sam Poe", party: "REP", votes: 900, pct: 47.4, candidateId: null },
          ],
        }),
      ],
      ...overrides,
    });
  const row = () =>
    within(screen.getByRole("region", { name: /By state/ })).getByRole("link", { name: /^GA/ });

  it("lists the uncounted race on the state's row as having no count", () => {
    render(<ResultsOverview now={NOW} results={twoSeats()} states={["GA"]} />);
    expect(row()).toHaveTextContent("Senate: Jane Roe (D) leads");
    expect(row()).toHaveTextContent("Senate (special): no count shown here");
  });

  it("names both races in the map's label", () => {
    render(<ResultsOverview now={NOW} results={twoSeats()} states={["GA"]} />);
    expect(mapProps.current!.getStateLabel("GA")).toBe(
      "GA Senate: regular race Democrat leads, 80% in; special race no count shown here"
    );
  });

  it("says the page's own failure, not the feed's, when it couldn't refresh", () => {
    render(<ResultsOverview now={NOW} results={twoSeats()} states={["GA"]} refreshFailed />);
    expect(row()).toHaveTextContent("Senate (special): no count as this page last read it");
  });

  it("reads as before against an older backend with no list", () => {
    render(
      <ResultsOverview now={NOW} results={twoSeats({ senateRaces: undefined })} states={["GA"]} />
    );
    expect(row()).toHaveTextContent("Senate: Jane Roe (D) leads");
    expect(row()).not.toHaveTextContent("special");
  });
});

describe("the Senate map's footnote", () => {
  it("says what a two-seat state is shaded by, as stateShade draws it", () => {
    render(<ResultsOverview now={NOW} results={results()} states={["GA"]} />);
    expect(
      screen.getByText(
        /shaded by the party leading more of its two races, grey when two parties lead equally many/
      )
    ).toBeInTheDocument();
    expect(screen.queryByText(/leading both/)).not.toBeInTheDocument();
  });
});

describe("when this page's own refresh has failed", () => {
  const withFeeds = () =>
    results({
      liveStates: ["GA", "TX"],
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T02:58:00Z", lastOkAt: "2026-11-04T02:58:00Z" },
        TX: { status: "failed", checkedAt: "2026-11-04T02:58:00Z", lastOkAt: null },
      },
    } as Partial<LiveResults>);
  const row = (state: string) =>
    within(screen.getByRole("region", { name: /By state/ })).getByRole("link", {
      name: new RegExp(`^${state}`),
    });

  it("no row says LIVE, and none blames the state's feed", () => {
    render(<ResultsOverview now={NOW} results={withFeeds()} states={["GA", "TX"]} refreshFailed />);
    for (const st of ["GA", "TX"]) {
      expect(row(st)).toHaveTextContent("NOT REFRESHED");
      expect(row(st)).not.toHaveTextContent(/LIVE|STALE|FEED NOT READ|couldn't read/);
    }
    expect(row("GA")).toHaveTextContent(/COUNT FROM/);
  });

  it("draws every count striped and names it not live, for the page's failure", () => {
    render(<ResultsOverview now={NOW} results={withFeeds()} states={["GA", "TX"]} refreshFailed />);
    fireEvent.click(screen.getByRole("button", { name: "HOUSE" }));
    const map = mapProps.current!;
    expect(map.getFillColor("GA")).toMatch(/^url\(#/);
    expect(map.getStateLabel("GA")).toMatch(/not live, this page couldn't refresh it, count from/);
    expect(map.getStateLabel("TX")).not.toMatch(/feed/);
    expect(screen.getByText(/NOT LIVE: THIS PAGE COULDN'T REFRESH/)).toBeInTheDocument();
    expect(screen.getByText(/This page couldn.t refresh the count/)).toBeInTheDocument();
    expect(screen.queryByText(/Amber stripes mean Civitas couldn/)).not.toBeInTheDocument();
  });

  it("is drawn live and solid again once a refresh answers", () => {
    render(<ResultsOverview now={NOW} results={withFeeds()} states={["GA", "TX"]} />);
    fireEvent.click(screen.getByRole("button", { name: "HOUSE" }));
    expect(row("GA")).toHaveTextContent("LIVE");
    expect(mapProps.current!.getFillColor("GA")).not.toMatch(/^url\(#/);
    expect(row("TX")).toHaveTextContent("FEED NOT READ");
  });
});

describe("a chamber with no count shown while the other has one", () => {
  const row = (state: string) =>
    within(screen.getByRole("region", { name: /By state/ })).getByRole("link", {
      name: new RegExp(`^${state}`),
    });

  it("says the Senate has no count shown, not no votes yet, on the map and the row", () => {
    // GA elects a senator; its feed counts a House race and no Senate one.
    render(
      <ResultsOverview now={NOW} results={results({ senateStates: ["GA"] })} states={["GA"]} />
    );
    const map = mapProps.current!;
    expect(map.getStateLabel("GA")).toBe("GA Senate: no count shown here");
    expect(map.getStateLabel("GA")).not.toMatch(/no votes/);
    // Hatched (a texture), not the dotted "no votes yet".
    expect(map.getFillColor("GA")).toMatch(/-nocount\)$/);
    expect(row("GA")).toHaveTextContent("Senate: no count shown here");
    expect(row("GA")).not.toHaveTextContent(/no votes/i);
    expect(screen.getByText(/NO COUNT SHOWN HERE/)).toBeInTheDocument();
  });

  it("says the House has no count shown when only the Senate race has one", () => {
    const senate = race({ raceId: "2026-SEN-GA", office: "S", district: null });
    render(
      <ResultsOverview
        now={NOW}
        results={results({ senateStates: ["GA"], races: [senate] })}
        states={["GA"]}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "HOUSE" }));
    const map = mapProps.current!;
    expect(map.getStateLabel("GA")).toBe("GA House: no count shown here");
    expect(map.getFillColor("GA")).toMatch(/-nocount\)$/);
    expect(row("GA")).toHaveTextContent("HOUSE: NO COUNT SHOWN HERE");
  });

  it("keeps 'no votes yet' for a state whose feed has given no count at all", () => {
    render(
      <ResultsOverview
        now={NOW}
        results={results({ senateStates: ["GA"], races: [] })}
        states={["GA"]}
      />
    );
    expect(mapProps.current!.getStateLabel("GA")).toBe("GA Senate: no votes yet");
    expect(row("GA")).toHaveTextContent("Senate: no votes yet");
    expect(row("GA")).not.toHaveTextContent(/HOUSE:/);
  });
});
