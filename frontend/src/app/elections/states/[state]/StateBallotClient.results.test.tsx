import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StateBallotClient from "./StateBallotClient";
import { RECHECK_FRAMES } from "@/hooks/useHashAt";
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
type MapProps = {
  results?: Map<number, unknown>;
  feedAnswered?: boolean;
  showLean?: boolean;
  stale?: boolean;
  onPick: (raceId: string) => void;
};
const districtMapProps = vi.hoisted(() => [] as MapProps[]);
vi.mock("@/components/elections/DistrictMap", () => ({
  default: (props: MapProps) => {
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
  vi.useRealTimers();
  vi.clearAllMocks();
  districtMapProps.length = 0;
  window.history.replaceState(null, "", "/");
});

/** Land on this state's page with `hash`, as a cold load does: the URL is
 * already the page's own when it first renders. */
function arriveAt(hash: string) {
  window.history.replaceState(null, "", `/elections/states/OH${hash}`);
}

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
    measureCoverage: {
      status: "not_yet_covered",
      sourceName: null,
      checkedAt: null,
      lastAttemptAt: null,
    },
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

/** Let the hash hook's animation-frame re-checks run (useHashAt), so a
 * "never scrolled" assertion can fail when a scroll is merely late. A
 * "never"/"only once" claim waits out every re-check (ALL_RECHECKS); the
 * default is short on purpose, for a test that needs re-checks left over. */
const ALL_RECHECKS = RECHECK_FRAMES + 5;
async function afterFrames(n = 5) {
  await act(async () => {
    for (let i = 0; i < n; i++) await new Promise((r) => requestAnimationFrame(() => r(null)));
  });
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
    arriveAt("#race-2026-HOUSE-OH-1");
    fetchLiveResults.mockResolvedValue(live());
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    // The hash is read after commit and re-checked on animation frames
    // (useHashAt), so the scroll lands a frame or more after the count
    // renders: wait for it rather than race it.
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends a #race- link reached by in-app navigation to the count", async () => {
    // The live-updates feed on /elections links here: a soft navigation
    // renders this page while the URL still reads /elections.
    window.history.replaceState(null, "", "/elections");
    fetchLiveResults.mockResolvedValue(live());
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    // Only a few frames, not ALL_RECHECKS: pushState fires no event, so it is
    // the hook's still-running re-check frames that must notice the push
    // below. Waiting them all out would leave nothing to see it.
    await afterFrames();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
    await act(async () => {
      window.history.pushState(null, "", "/elections/states/OH#race-2026-HOUSE-OH-1");
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    });
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens research for an in-app #race- link to a race with no count", async () => {
    window.history.replaceState(null, "", "/elections");
    fetchLiveResults.mockResolvedValue(live({ races: [] }));
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByText(/count hasn.t started yet/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => {
      window.history.pushState(null, "", "/elections/states/OH#race-2026-HOUSE-OH-1");
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
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

  it("words the national directory as a directory, not as the state's count", async () => {
    // With no verified state link the lookup is USAGov's office finder: the
    // link can't promise the count is at the other end.
    fetchLiveResults.mockResolvedValue(live({ liveStates: ["GA"], races: [] }));
    render(
      <StateBallotClient
        ballot={ballot({
          officialLookup: {
            url: "https://www.usa.gov/election-office",
            label: "Find your election office",
            sourceName: "USA.gov",
            isStateSpecific: false,
            verifiedAt: null,
          },
        })}
      />
    );
    await screen.findByRole("heading", { name: "No live count for Ohio here" });
    const link = screen.getByRole("link", {
      name: /Find Ohio's election office, which publishes it \(USAGov directory\)/,
    });
    expect(link).toHaveAttribute("href", "https://www.usa.gov/election-office");
    expect(
      screen.queryByRole("link", { name: /^Ohio's election office publishes/ })
    ).not.toBeInTheDocument();
  });

  it("treats a rejected state link as the national directory", async () => {
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(
      <StateBallotClient
        ballot={ballot({
          officialLookup: {
            url: "javascript:alert(1)",
            label: "Ohio Secretary of State",
            sourceName: "Ohio SOS",
            isStateSpecific: true,
            verifiedAt: null,
          },
        })}
      />
    );
    const alert = await screen.findByRole("alert");
    const link = within(alert).getByRole("link");
    expect(link).toHaveAttribute("href", "https://www.usa.gov/election-office");
    expect(link).toHaveTextContent(
      "Find Ohio's election office, which publishes the count (USAGov directory)"
    );
  });

  it("says the feed couldn't be read rather than that counting hasn't started", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
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
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
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

  it("says STALE, not live, when the backend stopped reading the feed with a count shown", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // Counts still moving, so the sync reads every five minutes: a read 20
    // minutes old is a stopped sync, though the read itself went fine.
    vi.setSystemTime(new Date("2026-11-04T03:20:00Z"));
    fetchLiveResults.mockResolvedValue(
      live({
        phase: { ...PHASE, lastResultChange: "2026-11-04T02:42:00Z" },
        feeds: {
          OH: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
        },
      })
    );
    render(<StateBallotClient ballot={ballot()} />);
    expect(
      await screen.findByText(
        /OHIO.S FEED HASN.T BEEN CHECKED SINCE NOV 3, 10:00 PM ET · THE COUNT BELOW WAS READ AT NOV 3, 10:00 PM ET/
      )
    ).toBeInTheDocument();
    const region = screen.getByText("STALE", { selector: "[role=status]" });
    // Not "UPDATED …": that reads as a count still being refreshed.
    expect(region.parentElement).not.toHaveTextContent(/UPDATED/);
    expect(region.parentElement).toHaveTextContent("STALE · THIS PAGE CHECKS EVERY MINUTE");
  });

  it("calls a state with no read record since its polls closed stale", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:20:00Z"));
    fetchLiveResults.mockResolvedValue(
      live({
        phase: { ...PHASE, lastResultChange: "2026-11-04T02:42:00Z" },
        pollsClose: { OH: "2026-11-04T00:30:00Z" },
        feeds: {},
      })
    );
    render(<StateBallotClient ballot={ballot()} />);
    expect(
      await screen.findByText(/NO RECORD OF OHIO.S FEED BEING CHECKED SINCE ITS POLLS CLOSED/)
    ).toBeInTheDocument();
    expect(screen.getByText("STALE", { selector: "[role=status]" })).toBeInTheDocument();
  });

  it("says the feed hasn't been read lately, with no count, rather than that counting hasn't started", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:20:00Z"));
    fetchLiveResults.mockResolvedValue(
      live({
        phase: { ...PHASE, lastResultChange: "2026-11-04T02:42:00Z" },
        races: [],
        feeds: { OH: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: null } },
      })
    );
    const two = [houseRace(), { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 }];
    render(<StateBallotClient ballot={ballot({ houseRaces: two })} />);
    expect(
      await screen.findByText(/hasn.t read Ohio.s results feed since Nov 3, 10:00 PM ET/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/hasn.t started yet/)).not.toBeInTheDocument();
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    expect(districtMapProps.every((p) => p.results === undefined)).toBe(true);
  });

  it("says when the polls close before any count is shown, in the present tense", async () => {
    const day = { ...PHASE, phase: "election_day" as const };
    fetchLiveResults.mockResolvedValue(
      live({ phase: day, races: [], pollsClose: { OH: "2099-11-04T00:30:00Z" } })
    );
    render(<StateBallotClient ballot={ballot({ phase: day })} />);
    expect(
      await screen.findByText(
        /last polls close at .* ET\. Nothing of its count is shown before then\./
      )
    ).toBeInTheDocument();
    // People are still voting: research first, nothing in the past tense.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Everyone on Ohio's ballot, and who is behind them"
    );
    expect(screen.queryByText(/^RESULTS ·/)).not.toBeInTheDocument();
    expect(screen.queryByText(/WHO WAS ON THE BALLOT/)).not.toBeInTheDocument();
    // …and no district drawn by the count, not even as "no votes yet".
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    expect(districtMapProps.every((p) => p.results === undefined)).toBe(true);
  });

  it("turns to the results framing once the state's polls have closed", async () => {
    const day = { ...PHASE, phase: "election_day" as const };
    fetchLiveResults.mockResolvedValue(
      live({ phase: day, races: [], pollsClose: { OH: "2020-11-04T00:30:00Z" } })
    );
    render(<StateBallotClient ballot={ballot({ phase: day })} />);
    await screen.findByText(/count hasn.t started yet/);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Ohio results");
    expect(screen.getByText(/WHO WAS ON THE BALLOT/)).toBeInTheDocument();
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

  it("shows no district lean beside the count: not in the picker, on a picked district or on the map", async () => {
    fetchLiveResults.mockResolvedValue(live());
    const two = [houseRace(), { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 }];
    render(<StateBallotClient ballot={ballot({ houseRaces: two })} />);
    await screen.findByRole("region", { name: "U.S. House" });
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    const drawer = within(screen.getByRole("dialog"));
    // The picker lists both districts, with no D+3 beside either.
    expect(drawer.getAllByRole("button", { name: /no funded Democrat/ })).toHaveLength(2);
    expect(drawer.queryByText("D+3")).not.toBeInTheDocument();
    expect(districtMapProps.at(-1)?.showLean).toBe(false);
    await userEvent.click(drawer.getAllByRole("button", { name: /no funded Democrat/ })[1]);
    const picked = within(screen.getByRole("dialog"));
    expect(picked.getByText("District 2")).toBeInTheDocument();
    expect(picked.queryByText("D+3")).not.toBeInTheDocument();
  });

  it("opens research for a #race- link to a race with no count", async () => {
    arriveAt("#race-2026-HOUSE-OH-1");
    fetchLiveResults.mockResolvedValue(live({ races: [] }));
    render(<StateBallotClient ballot={ballot()} />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("moves keyboard focus to the race a #race- link lands on, not only the scroll", async () => {
    arriveAt("#race-2026-HOUSE-OH-1");
    fetchLiveResults.mockResolvedValue(live());
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    await waitFor(() => expect(document.activeElement?.id).toBe("result-2026-HOUSE-OH-1"));
  });

  it("focuses a Senate race's card when a #race- link lands on it", async () => {
    arriveAt("#race-2026-SEN-OH");
    const senate = {
      ...live().races[0],
      raceId: "2026-SEN-OH",
      office: "S" as const,
      district: null,
    };
    fetchLiveResults.mockResolvedValue(live({ races: [senate] }));
    render(<StateBallotClient ballot={ballot()} />);
    await waitFor(() => expect(document.activeElement?.id).toBe("result-2026-SEN-OH"));
    expect(document.activeElement?.tagName).toBe("ARTICLE");
    expect(document.activeElement).toHaveAttribute("tabindex", "-1");
  });

  it("says when the count was read, and that a failed refresh leaves an older one on screen", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:10:00Z"));
    fetchLiveResults.mockResolvedValueOnce(live()).mockRejectedValue(new Error("502"));
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    const line = screen.getByText(/THIS PAGE CHECKS EVERY MINUTE/).parentElement!;
    // The live region is the state alone: its time is beside it, so a new
    // read each pass isn't announced again.
    const region = within(line).getByRole("status");
    expect(region).toHaveTextContent(/^LIVE$/);
    // The backend's own read time, never the page's clock.
    expect(line).toHaveTextContent(
      "LIVE · UPDATED NOV 3, 9:44 PM ET · THIS PAGE CHECKS EVERY MINUTE"
    );
    // The next refresh fails: the count stays, and the line says it is old.
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() =>
      expect(line).toHaveTextContent(
        "REFRESH FAILED · AT NOV 3, 10:10 PM ET · SHOWING THE COUNT READ AT NOV 3, 9:44 PM ET · RETRYING EVERY MINUTE"
      )
    );
    expect(region).toHaveTextContent(/^REFRESH FAILED$/);
    expect(line).not.toHaveTextContent(/UPDATED/);
    expect(screen.getByText("Eric Conroy")).toBeInTheDocument();
    // The district map no longer draws the old count solid, as if live.
    const counted = districtMapProps.filter((p) => p.results !== undefined);
    expect(counted.at(-1)?.stale).toBe(true);
  });

  it("dates a failed refresh on the server's clock, not a slow browser's", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // Three hours slow: the server's Date says 03:10.
    vi.setSystemTime(new Date("2026-11-04T00:10:00Z"));
    fetchLiveResults
      .mockResolvedValueOnce(
        live({ clock: { serverDate: Date.parse("2026-11-04T03:10:00Z"), receivedAt: Date.now() } })
      )
      .mockRejectedValue(new Error("502"));
    render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    const line = screen.getByText(/THIS PAGE CHECKS EVERY MINUTE/).parentElement!;
    vi.setSystemTime(new Date("2026-11-04T00:11:00Z"));
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(line).toHaveTextContent("REFRESH FAILED · AT NOV 3, 10:11 PM ET"));
  });

  it("scrolls to the count once, not again when the page writes its own hash", async () => {
    arriveAt("#race-2026-HOUSE-OH-1");
    fetchLiveResults.mockResolvedValue(live());
    const { rerender } = render(<StateBallotClient ballot={ballot()} />);
    await screen.findByRole("region", { name: "U.S. House" });
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1));
    rerender(<StateBallotClient ballot={ballot()} />);
    await afterFrames(ALL_RECHECKS);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("says the count couldn't load, with the state's own office, instead of loading forever", async () => {
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(<StateBallotClient ballot={ballot()} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The live count couldn't be loaded");
    expect(within(alert).getByRole("link")).toHaveAttribute("href", "https://www.sos.state.oh.us/");
  });

  it("keeps research open for a #race- arrival when the race's first count lands later", async () => {
    arriveAt("#race-2026-HOUSE-OH-1");
    fetchLiveResults.mockResolvedValueOnce(live({ races: [] }));
    render(<StateBallotClient ballot={ballot()} />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    // The next poll (a tab shown again asks at once) brings first returns.
    fetchLiveResults.mockResolvedValue(live());
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await screen.findByRole("region", { name: "U.S. House" });
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    // Decided once: the drawer stays, and the page doesn't jump to the count.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await afterFrames(ALL_RECHECKS);
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });

  it("hands a #race- arrival to research when the count fails to load", async () => {
    arriveAt("#race-2026-HOUSE-OH-1");
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(<StateBallotClient ballot={ballot()} />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("This page retries every minute.");
  });

  it("gives a state whose feed failed, with no count, no count-shaded map", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchLiveResults.mockResolvedValue(
      live({
        races: [],
        feeds: { OH: { status: "failed", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: null } },
      })
    );
    const two = [houseRace(), { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 }];
    render(<StateBallotClient ballot={ballot({ houseRaces: two })} />);
    await screen.findByText(/couldn.t read Ohio.s results feed/);
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    expect(districtMapProps.length).toBeGreaterThan(0);
    expect(districtMapProps.every((p) => p.results === undefined)).toBe(true);
  });

  it("words the House drawer for results mode, not as a voter's instruction", async () => {
    fetchLiveResults.mockResolvedValue(live());
    const two = [houseRace(), { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 }];
    render(<StateBallotClient ballot={ballot({ houseRaces: two, newDistrictLines: true })} />);
    await screen.findByRole("region", { name: "U.S. House" });
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    expect(screen.getAllByText(/2 districts · one per voter/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/you vote in one/)).not.toBeInTheDocument();
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    const drawer = within(screen.getByRole("dialog"));
    const intro = drawer.getByText(/new congressional district lines/).closest("p");
    expect(intro).toHaveTextContent(
      /^Each voter has exactly one of these on the ballot\. This year's election in Ohio is on new congressional district lines, so your district/
    );
    expect(intro).not.toHaveTextContent(/You vote in|votes on new/);
  });

  it("words the House drawer for results mode on today's lines too", async () => {
    fetchLiveResults.mockResolvedValue(live());
    const two = [houseRace(), { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 }];
    render(<StateBallotClient ballot={ballot({ houseRaces: two })} />);
    await screen.findByRole("region", { name: "U.S. House" });
    const index = screen.getByRole("navigation", { name: "Contests on this ballot" });
    await userEvent.click(within(index).getByRole("button", { name: /U.S. Representative/ }));
    const drawer = within(screen.getByRole("dialog"));
    expect(
      drawer.getByText(/Each voter has exactly one of these on the ballot\./)
    ).toBeInTheDocument();
    expect(drawer.queryByText(/You vote in/)).not.toBeInTheDocument();
  });

  it("says the count has ended when the results window closes while the page is open", async () => {
    fetchLiveResults.mockResolvedValue(
      live({
        phase: {
          phase: "campaign",
          electionDate: "2028-11-07",
          resultsUntil: null,
          lastResultChange: null,
        },
        races: [],
      })
    );
    render(<StateBallotClient ballot={ballot()} />);
    expect(
      await screen.findByRole("heading", { name: "The live count has ended here" })
    ).toBeInTheDocument();
    expect(screen.queryByText(/hasn.t started yet/)).not.toBeInTheDocument();
  });

  it("switches a campaign page into results when election day arrives while it's open", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
    fetchLiveResults.mockResolvedValue(live({ phase: { ...PHASE, phase: "election_day" } }));
    render(
      <StateBallotClient
        ballot={ballot({
          phase: {
            phase: "campaign",
            electionDate: "2026-11-03",
            resultsUntil: null,
            lastResultChange: null,
          },
        })}
      />
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Ohio results")
    );
    expect(await screen.findByRole("region", { name: "U.S. House" })).toBeInTheDocument();
  });

  it("starts asking when election day comes near while a campaign page is open", async () => {
    // Nothing re-renders an idle campaign page, so the page arms its own
    // wake-up for 36 hours before the day.
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date("2026-11-01T11:00:00Z"));
    fetchLiveResults.mockResolvedValue(live({ phase: { ...PHASE, phase: "campaign" } }));
    render(
      <StateBallotClient
        ballot={ballot({
          phase: {
            phase: "campaign",
            electionDate: "2026-11-03",
            resultsUntil: null,
            lastResultChange: null,
          },
        })}
      />
    );
    await act(async () => vi.advanceTimersByTime(59 * 60_000));
    expect(fetchLiveResults).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
  });

  it("asks nothing from a campaign page far from election day", () => {
    render(
      <StateBallotClient
        ballot={ballot({
          phase: {
            phase: "campaign",
            electionDate: "2099-11-03",
            resultsUntil: null,
            lastResultChange: null,
          },
        })}
      />
    );
    expect(fetchLiveResults).not.toHaveBeenCalled();
  });

  it("says why no House seat in a state on new lines is marked as changing party", async () => {
    const base = live().races[0];
    fetchLiveResults.mockResolvedValue(
      live({ redrawnStates: ["OH", "TX"], races: [{ ...base, heldBy: null, flip: false }] })
    );
    render(<StateBallotClient ballot={ballot()} />);
    const house = await screen.findByRole("region", { name: "U.S. House" });
    expect(
      within(house).getByText(/New district lines this year: no seat has a previous holder/)
    ).toBeInTheDocument();
  });

  it("adds no new-lines note for a state on its old lines", async () => {
    fetchLiveResults.mockResolvedValue(live({ redrawnStates: ["TX"] }));
    render(<StateBallotClient ballot={ballot()} />);
    const house = await screen.findByRole("region", { name: "U.S. House" });
    expect(within(house).queryByText(/New district lines/)).not.toBeInTheDocument();
  });

  it("moves focus to the row of a district picked on the results map", async () => {
    fetchLiveResults.mockResolvedValue(live());
    render(<StateBallotClient ballot={ballot()} />);
    const house = await screen.findByRole("region", { name: "U.S. House" });
    const map = districtMapProps.find((p) => p.results?.size);
    expect(map).toBeDefined();
    act(() => map!.onPick("2026-HOUSE-OH-1"));
    expect(document.activeElement).toBe(within(house).getByRole("listitem"));
  });

  it("lists every district, saying which the feed gives no count for, and lands a pick there", async () => {
    const three = [
      houseRace(),
      { ...houseRace(), id: "2026-HOUSE-OH-2", district: 2 },
      { ...houseRace(), id: "2026-HOUSE-OH-3", district: 3 },
    ];
    const base = live().races[0];
    fetchLiveResults.mockResolvedValue(
      live({
        races: [
          base,
          {
            ...base,
            raceId: "2026-HOUSE-OH-3",
            district: 3,
            leaderParty: "IND",
            flip: false,
            candidates: [
              { name: "Ind Person", party: "IND", votes: 600, pct: 60, candidateId: null },
              { name: "Greg Landsman", party: "DEM", votes: 400, pct: 40, candidateId: null },
            ],
          },
        ],
      })
    );
    render(<StateBallotClient ballot={ballot({ houseRaces: three })} />);
    const house = await screen.findByRole("region", { name: "U.S. House" });
    const rows = within(house).getAllByRole("listitem");
    expect(rows.map((r) => r.id)).toEqual([
      "result-2026-HOUSE-OH-1",
      "result-2026-HOUSE-OH-2",
      "result-2026-HOUSE-OH-3",
    ]);
    expect(rows[1]).toHaveTextContent("OH-2No count from the state's feed");
    expect(rows[1]).not.toHaveTextContent(/no votes/i);
    // The tally counts the independent's lead too.
    expect(house).toHaveTextContent("D 0 · R 1 · I 1 LEADING");
    const map = districtMapProps.find((p) => p.results?.size);
    expect(map?.feedAnswered).toBe(true);
    act(() => map!.onPick("2026-HOUSE-OH-2"));
    expect(document.activeElement).toBe(rows[1]);
  });
});
