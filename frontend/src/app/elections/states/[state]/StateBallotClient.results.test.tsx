import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StateBallotClient from "./StateBallotClient";
import type { LiveResults, RaceWithCandidates, StateBallot } from "@/types/election";

/* The state page from election day on: the live count above the ballot
   research, a #race- link landing on the count, and a state with no feed
   saying so instead of drawing an empty section. */

const fetchLiveResults = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  fetchTownsForState: vi.fn().mockResolvedValue([]),
  fetchTownBallot: vi.fn(),
  fetchLiveResults,
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));
// Records what each district map was handed, so a test can tell a
// count-shaded map from a lean-shaded one.
const districtMapProps = vi.hoisted(() => [] as { results?: Map<number, unknown> }[]);
vi.mock("@/components/elections/DistrictMap", () => ({
  default: (props: { results?: Map<number, unknown> }) => {
    districtMapProps.push(props);
    return null;
  },
}));
vi.mock("@/components/elections/CoverageFeed", async () => {
  const actual = await vi.importActual<typeof import("@/components/elections/CoverageFeed")>(
    "@/components/elections/CoverageFeed"
  );
  return { ...actual, default: () => <div /> };
});
Element.prototype.scrollIntoView = vi.fn();

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  districtMapProps.length = 0;
  window.location.hash = "";
});

const PHASE = {
  phase: "results" as const,
  electionDate: "2026-11-03",
  resultsUntil: "2026-11-20",
  lastResultChange: "2026-11-04T02:42:00Z",
};

function houseRace(): RaceWithCandidates {
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
    counties: [],
    candidates: [],
  };
}

function ballot(overrides: Partial<StateBallot> = {}): StateBallot {
  return {
    state: "OH",
    stateName: "Ohio",
    cycleYear: 2026,
    electionDate: "2026-11-03",
    electionType: "general",
    phase: PHASE,
    primaryDate: null,
    ballotBasis: {
      basis: "filers",
      primaryPassed: null,
      daysSincePrimary: null,
      supersededByPrimary: false,
    },
    statePvi: 6,
    senateRaces: [],
    nextSenateElection: null,
    houseRaces: [houseRace()],
    coverage: [],
    measures: [],
    measureCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
    statewideRaces: [],
    statewideCoverage: { status: "not_yet_covered", sourceName: null, checkedAt: null },
    stateLegRaces: [],
    judicialRaces: [],
    judicialCoverage: { status: "not_yet_covered", checkedAt: null, sourceName: null },
    officialLookup: {
      url: "https://www.sos.state.oh.us/",
      label: "Ohio Secretary of State",
      sourceName: "Ohio SOS",
      isStateSpecific: true,
      verifiedAt: null,
    },
    omits: [],
    ...overrides,
  };
}

function live(overrides: Partial<LiveResults> = {}): LiveResults {
  return {
    cycleYear: 2026,
    phase: PHASE,
    liveStates: ["OH"],
    senateStates: [],
    pollsClose: {},
    races: [
      {
        raceId: "2026-HOUSE-OH-1",
        state: "OH",
        office: "H",
        district: 1,
        isSpecial: false,
        heldBy: "DEM",
        official: false,
        votesCounted: 1000,
        reportingUnits: 300,
        totalUnits: 400,
        unitLabel: "precincts",
        sourceName: "Ohio Secretary of State",
        sourceUrl: "https://results.example/oh",
        fetchedAt: "2026-11-04T02:44:00Z",
        lastChangeAt: "2026-11-04T02:42:00Z",
        leaderParty: "REP",
        flip: true,
        candidates: [
          { name: "Eric Conroy", party: "REP", votes: 560, pct: 56, candidateId: "rep1" },
          { name: "Greg Landsman", party: "DEM", votes: 440, pct: 44, candidateId: "dem1" },
        ],
      },
    ],
    updates: [],
    ...overrides,
  };
}

describe("the state page in results mode", () => {
  it("leads with the count, research below", async () => {
    fetchLiveResults.mockResolvedValue(live());
    render(<StateBallotClient ballot={ballot()} />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Ohio results");
    const house = await screen.findByRole("region", { name: "U.S. House" });
    expect(within(house).getByText("Eric Conroy")).toBeInTheDocument();
    expect(within(house).getByText("FLIP · LEADING")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /BALLOT RESEARCH/ })).toBeInTheDocument();
    expect(fetchLiveResults).toHaveBeenCalledWith("OH");
  });

  it("sends a #race- link to the count, not the research drawer", async () => {
    window.location.hash = "#race-2026-HOUSE-OH-1";
    fetchLiveResults.mockResolvedValue(live());
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("says a state with no feed has no count here", async () => {
    fetchLiveResults.mockResolvedValue(live({ liveStates: ["GA"], races: [] }));
    render(<StateBallotClient ballot={ballot()} />);
    expect(
      await screen.findByRole("heading", { name: "No live count for Ohio here" })
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /election office publishes it/ })).toHaveAttribute(
      "href",
      "https://www.sos.state.oh.us/"
    );
  });

  it("says the feed couldn't be read rather than that counting hasn't started", async () => {
    fetchLiveResults.mockResolvedValue(
      live({
        races: [],
        feeds: { OH: { status: "unavailable", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: null } },
      })
    );
    render(<StateBallotClient ballot={ballot()} />);
    const note = await screen.findByText(
      /couldn.t read Ohio.s results feed \(last tried Nov 3, 10:00 PM ET\)/
    );
    expect(note).toBeInTheDocument();
    expect(screen.queryByText(/hasn.t started yet/)).not.toBeInTheDocument();
    expect(within(note).getByRole("link")).toHaveAttribute("href", "https://www.sos.state.oh.us/");
  });

  it("says when the count shown is older than a failed read", async () => {
    fetchLiveResults.mockResolvedValue(
      live({
        feeds: {
          OH: {
            status: "untrusted",
            checkedAt: "2026-11-04T03:00:00Z",
            lastOkAt: "2026-11-04T02:44:00Z",
          },
        },
      })
    );
    render(<StateBallotClient ballot={ballot()} />);
    expect(
      await screen.findByText(/COULDN.T BE USED · THE COUNT BELOW WAS READ AT NOV 3, 9:44 PM ET/)
    ).toBeInTheDocument();
  });

  it("says when the polls close before any count is shown", async () => {
    fetchLiveResults.mockResolvedValue(
      live({ races: [], pollsClose: { OH: "2099-11-04T00:30:00Z" } })
    );
    render(<StateBallotClient ballot={ballot()} />);
    expect(
      await screen.findByText(
        /last polls close at .* ET\. Nothing of its count is shown before then\./
      )
    ).toBeInTheDocument();
  });

  it("asks for no results during a campaign", () => {
    render(<StateBallotClient ballot={ballot({ phase: undefined })} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Everyone on Ohio/);
    expect(fetchLiveResults).not.toHaveBeenCalled();
  });

  it("gives a state with no feed no count-shaded map, even in its research", async () => {
    fetchLiveResults.mockResolvedValue(live({ liveStates: ["GA"], races: [] }));
    // Two districts: with one, the drawer shows that race and no map.
    const two = [houseRace(), { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 }];
    render(<StateBallotClient ballot={ballot({ houseRaces: two })} />);
    await screen.findByRole("heading", { name: "No live count for Ohio here" });
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    expect(districtMapProps.length).toBeGreaterThan(0);
    expect(districtMapProps.every((p) => p.results === undefined)).toBe(true);
  });

  it("opens research for a #race- link to a race with no count", async () => {
    window.location.hash = "#race-2026-HOUSE-OH-1";
    fetchLiveResults.mockResolvedValue(live({ races: [] }));
    render(<StateBallotClient ballot={ballot()} />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("scrolls to the count once, not again when the page writes its own hash", async () => {
    window.location.hash = "#race-2026-HOUSE-OH-1";
    fetchLiveResults.mockResolvedValue(live());
    const { rerender } = render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1));
    rerender(<StateBallotClient ballot={ballot()} />);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("says the count couldn't load, with the state's own office, instead of loading forever", async () => {
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(<StateBallotClient ballot={ballot()} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The live count couldn't be loaded");
    expect(within(alert).getByRole("link")).toHaveAttribute("href", "https://www.sos.state.oh.us/");
  });
});
