import { describe, expect, it, vi } from "vitest";
import { afterEach, beforeEach } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import ElectionsPage from "./page";
import { ElectionsInitialProvider, type ElectionsInitial } from "./ElectionsInitial";

const fetchPviMap = vi.hoisted(() => vi.fn());
const fetchLiveResults = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchPviMap, fetchLiveResults }));

const CAMPAIGN = {
  cycleYear: 2026,
  phase: {
    phase: "campaign",
    electionDate: "2026-11-03",
    resultsUntil: null,
    lastResultChange: null,
  },
  liveStates: ["GA"],
  senateStates: ["GA"],
  races: [],
  updates: [],
};

const RESULTS = {
  ...CAMPAIGN,
  phase: {
    phase: "results",
    electionDate: "2026-11-03",
    resultsUntil: "2026-11-20",
    lastResultChange: "2026-11-04T02:42:00Z",
  },
  races: [
    {
      raceId: "2026-SEN-GA",
      state: "GA",
      office: "S",
      district: null,
      isSpecial: false,
      heldBy: "DEM",
      official: false,
      votesCounted: 1900,
      reportingUnits: 2103,
      totalUnits: 2653,
      unitLabel: "precincts",
      sourceName: "Georgia Secretary of State",
      sourceUrl: "https://results.example/ga",
      fetchedAt: "2026-11-04T02:44:00Z",
      lastChangeAt: "2026-11-04T02:42:00Z",
      leaderParty: "REP",
      flip: true,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
      ],
    },
  ],
  updates: [
    {
      id: 1,
      raceId: "2026-SEN-GA",
      state: "GA",
      office: "S",
      district: null,
      isSpecial: false,
      kind: "flip",
      at: "2026-11-04T02:42:00Z",
      detail: {
        leader: { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6 },
        runnerUp: { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4 },
        reportingUnits: 2103,
        totalUnits: 2653,
        unitLabel: "precincts",
        heldBy: "DEM",
      },
    },
  ],
};
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// RaceMap pulls in react-simple-maps and a topojson payload; the page's own
// behaviour is what is under test here, not the map's rendering.
const mapFill = vi.hoisted(() => ({
  current: null as null | ((s: string) => string),
  label: null as null | ((s: string) => string),
  renders: 0,
}));
vi.mock("@/components/elections/RaceMap", () => ({
  default: (props: {
    getFillColor: (s: string) => string;
    getStateLabel?: (s: string) => string;
  }) => {
    mapFill.current = props.getFillColor;
    mapFill.label = props.getStateLabel ?? null;
    mapFill.renders += 1;
    return <div data-testid="race-map" />;
  },
  FIPS_TO_STATE: { "13": "GA", "36": "NY", "11": "DC" },
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

/** The masthead's status line: its live region (the page's state alone)
 * and the times beside it, outside the region. */
function statusRegion(): HTMLElement {
  return document.querySelector("header [role=status]") as HTMLElement;
}
function statusLine(): HTMLElement {
  return statusRegion().parentElement as HTMLElement;
}

beforeEach(() => {
  fetchLiveResults.mockResolvedValue(CAMPAIGN);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("ElectionsPage", () => {
  it.each([
    ["the campaign", CAMPAIGN],
    ["election day", { ...RESULTS, phase: { ...RESULTS.phase, phase: "election_day" } }],
    ["the results window", RESULTS],
  ])("keeps every elections tab in every phase: %s", async (_label, live) => {
    // The masthead's links to the map and the new House maps rendered in
    // the campaign only, so from election day nothing on the page led to
    // either; a tab bar never drops a tab, and an out-of-phase page says why.
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue(live);
    render(<ElectionsPage />);
    const nav = await screen.findByRole("navigation", { name: "Elections sections" });
    // The wide row (a phone's switcher lists the same links).
    const tabs = within(nav.querySelector<HTMLElement>("[data-section-row]")!).getAllByRole("link");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Ballots by state",
      "Seat map",
      "New House maps",
      "Calendar",
      "Archive",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-current", "page");
    expect(tabs[3]).toHaveAttribute("href", "/elections/calendar");
    expect(tabs[4]).toHaveAttribute("href", "/elections/archive");
    expect(tabs[1]).toHaveAttribute("href", "/elections/map?chamber=senate&view=usual");
    // The inline masthead links, which ran off a phone screen, are gone.
    expect(screen.queryByText(/POLL RANGES →/)).not.toBeInTheDocument();
  });

  it("puts the state picker first, registration in one line, and the FEC deadline after the directory", async () => {
    // A voter on a phone scrolled about three screens, past the FEC's
    // filing deadline and every state closing registration, to pick a state.
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    const closing = (state: string, stateName: string, closesOn: string, daysLeft: number) => ({
      state,
      stateName,
      sentence: `Registration closes ${closesOn}`,
      closesOn,
      daysLeft,
      countdown: `${daysLeft} days`,
      sameDay: null,
      register: null,
    });
    const initial: ElectionsInitial = {
      pvi: { states: { GA: 3 }, districts: {}, cycleYear: 2026 } as never,
      phase: null,
      specials: null,
      calendar: null,
      renderedAt: Date.parse("2026-10-20T12:00:00Z"),
      fecDeadline: {
        date: "2026-10-22",
        label: "Pre-general report",
        reportType: "12G",
        reportYear: 2026,
        status: "upcoming",
        daysLeft: 2,
        daysAgo: null,
        leads: true,
        source: "https://www.fec.gov/",
      },
      registrationClosing: [
        closing("TX", "Texas", "2026-10-24", 4),
        closing("GA", "Georgia", "2026-10-21", 1),
      ],
      closingSoonDays: 10,
    };
    render(
      <ElectionsInitialProvider value={initial}>
        <ElectionsPage />
      </ElectionsInitialProvider>
    );
    const picker = await screen.findByRole("region", { name: "Your state's ballot" });
    const select = within(picker).getByRole("combobox", { name: "State" });
    // Never focused by itself: a phone opens its picker on focus.
    expect(select).not.toHaveFocus();
    const map = await screen.findByTestId("race-map");
    const follows = (a: Node, b: Node) =>
      !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(follows(picker, map)).toBe(true);
    // Registration closing: one line to the calendar, not a row per state.
    const line = screen.getByRole("link", { name: /Registration closes in the next 10 days/ });
    expect(line).toHaveTextContent("in 2 states, soonest tomorrow");
    expect(line).toHaveAttribute("href", "/elections/calendar");
    expect(screen.queryByRole("link", { name: "Texas" })).not.toBeInTheDocument();
    // The FEC's filing deadline is for candidates: after the directory.
    const fec = screen.getByRole("region", { name: /FEC|report/i });
    expect(follows(screen.getByText("All states"), fec)).toBe(true);
  });

  it("leads with the specials and the calendar between general elections", async () => {
    // In an odd year the page used to be a countdown to a general a year or
    // more off and the same lean map; the backend's season now says the
    // general isn't in its campaign, and the specials board leads.
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2028 });
    const season = { season: "off_season", betweenCycles: true, cycle: 2028, firstPrimary: null };
    const initial = {
      pvi: { states: { GA: 3 }, districts: {}, cycleYear: 2028 },
      phase: null,
      renderedAt: Date.parse("2027-06-01T12:00:00Z"),
      calendar: null,
      specials: {
        asOf: "2027-06-01",
        vacancies: [],
        vacanciesReadAt: null,
        upcoming: [],
        recent: [],
        lastSpecial: null,
        averageSwing: { swing: null, count: 0, of: 0, since: "2026-06-01", sentence: null },
        sources: { fec: "https://www.fec.gov/data/election-dates/" },
        season,
      },
    } as unknown as ElectionsInitial;
    render(
      <ElectionsInitialProvider value={initial}>
        <ElectionsPage />
      </ElectionsInitialProvider>
    );
    expect(await screen.findByRole("heading", { name: "Between general elections" })).toBeTruthy();
    expect(screen.getByText(/The 2028 primaries are not on the FEC's calendar yet/)).toBeTruthy();
    expect(screen.getByText(/Every seat in Congress is filled/)).toBeTruthy();
    expect(screen.getByText(/The calendar couldn't be read just now/)).toBeTruthy();
  });

  function seasonInitial(season: Record<string, unknown>, extra: Record<string, unknown> = {}) {
    return {
      pvi: { states: { GA: 3 }, districts: {}, cycleYear: 2028, electionDay: "2028-11-07" },
      phase: null,
      renderedAt: Date.parse("2026-12-15T12:00:00Z"),
      calendar: null,
      archive: null,
      specials: {
        asOf: "2026-12-15",
        vacancies: [],
        vacanciesReadAt: null,
        upcoming: [],
        recent: [],
        lastSpecial: null,
        averageSwing: { swing: null, count: 0, of: 0, since: "2025-12-15", sentence: null },
        sources: { fec: "https://www.fec.gov/data/election-dates/" },
        season: {
          cycle: 2028,
          electionDay: "2028-11-07",
          heldCycle: 2026,
          until: null,
          firstPrimary: null,
          lastPrimary: null,
          lead: [],
          ...season,
        },
      },
      ...extra,
    } as unknown as ElectionsInitial;
  }

  it("after the count settles, leads with the election as kept, and no countdown", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2028 });
    const initial = seasonInitial(
      { season: "post_certification", betweenCycles: true, until: "2027-01-02" },
      {
        archive: {
          current: { cycle: 2028, electionDay: "2028-11-07", phase: "campaign" },
          cycles: [
            {
              cycle: 2026,
              electionDay: "2026-11-03",
              races: 470,
              counted: 400,
              official: 312,
              measures: 120,
              states: 50,
              recordAsOf: null,
              mapAsOf: null,
              hasMap: true,
              stillCounting: false,
              accountability: {
                ranged: 35,
                certified: 30,
                official: 30,
                graded: 30,
                held80: 25,
                held95: 29,
                swingSeats: 30,
              },
            },
          ],
        },
      }
    );
    render(
      <ElectionsInitialProvider value={initial}>
        <ElectionsPage />
      </ElectionsInitialProvider>
    );
    expect(
      await screen.findByRole("heading", { name: "The 2026 election, on the record" })
    ).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("2026 midterm results");
    expect(
      screen.getByText("312 of 400 counted races listed official by their states")
    ).toBeTruthy();
    expect(screen.getByText(/25 of 30 margins inside the 80% range/)).toBeTruthy();
    expect(screen.getByText(/seated on January 3, 2027/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "[ THE 2026 RECORD → ]" })).toHaveAttribute(
      "href",
      "/elections/2026"
    );
    // A general two years off is no countdown.
    expect(screen.queryByText(/days? away/)).not.toBeInTheDocument();
    // The specials and the calendar still follow.
    expect(screen.getByRole("heading", { name: "Between general elections" })).toBeTruthy();
  });

  it("before the primaries counts down to the first of them, not the general", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2028 });
    render(
      <ElectionsInitialProvider
        value={seasonInitial({
          season: "off_season",
          betweenCycles: true,
          firstPrimary: "2028-01-15",
        })}
      >
        <ElectionsPage />
      </ElectionsInitialProvider>
    );
    expect(await screen.findByText(/First primary/)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "2028 elections: before the primaries"
    );
  });

  it("in primary season leads with the primaries ahead", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2028 });
    const day = (
      date: string,
      state: string,
      stateName: string,
      typeCode: string,
      label: string
    ) => ({
      id: `${date}-${state}`,
      date,
      state,
      stateName,
      office: "P",
      district: null,
      seat: stateName,
      typeCode,
      label,
      special: false,
      note: null,
      status: "upcoming",
      daysLeft: 5,
    });
    const initial = seasonInitial(
      {
        season: "ramp",
        betweenCycles: false,
        firstPrimary: "2028-01-15",
        lastPrimary: "2028-09-26",
      },
      {
        renderedAt: Date.parse("2028-02-01T12:00:00Z"),
        calendar: {
          elections: [
            day("2028-02-06", "NV", "Nevada", "P", "Primary election"),
            day("2028-02-06", "NV", "Nevada", "CAU", "Caucus"),
            {
              ...day("2028-02-10", "FL", "Florida", "SG", "Special election general"),
              special: true,
            },
          ],
          voterDeadlines: [],
          fecDeadlines: [],
        },
      }
    );
    render(
      <ElectionsInitialProvider value={initial}>
        <ElectionsPage />
      </ElectionsInitialProvider>
    );
    expect(await screen.findByRole("heading", { name: "The 2028 primaries" })).toBeTruthy();
    expect(screen.getByText(/run through September 26, 2028/)).toBeTruthy();
    expect(screen.getByText("Nevada (Primary election), Nevada (Caucus)")).toBeTruthy();
    // The special is not a primary (the state picker lists every state).
    const primaries = screen
      .getByRole("heading", { name: "The 2028 primaries" })
      .closest("section") as HTMLElement;
    expect(within(primaries).queryByText(/Florida/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "2028 elections: primary season"
    );
  });

  it("still renders the directory when the payload carries no lean data", async () => {
    // A /pvi response missing `states` used to take the whole page down with
    // "Cannot read properties of undefined (reading 'AK')" — a white screen,
    // not a degraded map. `meta` on this same response is already documented
    // as possibly missing on older or cached backend responses, and nothing
    // validates the shape on the way in.
    fetchPviMap.mockResolvedValue({ districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);

    expect(await screen.findByRole("link", { name: /GA/ })).toBeInTheDocument();
    expect(screen.getByTestId("race-map")).toBeInTheDocument();
  });

  it("counts each lean direction for the map key", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3, NY: -10 }, districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);

    expect(await screen.findByText(/D-LEANING/)).toBeInTheDocument();
    expect(screen.getByText(/R-LEANING/)).toBeInTheDocument();
  });

  it("keeps DC out of the directory — it has no federal race to link to", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);

    await screen.findByTestId("race-map");
    expect(screen.queryByRole("link", { name: /^DC/ })).not.toBeInTheDocument();
  });

  it("does not re-render once a second outside election night", async () => {
    // The page reads the clock only on election day before any count; a
    // page that subscribed all year re-rendered the map and every state row
    // once a second.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    render(<ElectionsPage />);
    await screen.findByText(/D-LEANING/);
    const settled = mapFill.renders;
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(mapFill.renders).toBe(settled);
  });

  it("surfaces a fetch failure instead of hanging on the loading line", async () => {
    fetchPviMap.mockRejectedValue(new Error("Failed to load election data"));
    render(<ElectionsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load election data");
  });

  it("leads with the live count from election day on", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue(RESULTS);
    render(<ElectionsPage />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "2026 midterm results"
    );
    expect(screen.getByText(/leads in a seat Democrats hold/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SENATE" })).toHaveAttribute("aria-pressed", "true");
    // The lean key is gone: the map now shades by the count.
    expect(screen.queryByText(/D-LEANING/)).not.toBeInTheDocument();
    expect(statusRegion()).toHaveTextContent(/^LIVE$/);
    expect(statusLine()).toHaveTextContent("LIVE · LAST CHANGE NOV 3, 9:42 PM ET");
    // How often the count is really read, not "refreshed every minute".
    expect(
      screen.getByText(
        /reads each state.s feed every five minutes .* checks for a new read every minute/
      )
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/until the state calls/);
  });

  it("keeps the campaign page when the backend predates the phase", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockRejectedValue(new Error("404"));
    render(<ElectionsPage />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "2026 midterm ballot"
    );
  });

  it("names a presidential cycle as one, never a midterm", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2028 });
    fetchLiveResults.mockResolvedValue({
      ...CAMPAIGN,
      cycleYear: 2028,
      phase: { ...CAMPAIGN.phase, electionDate: "2028-11-07" },
    });
    render(<ElectionsPage />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "2028 presidential election ballot"
    );
    expect(document.body).not.toHaveTextContent(/midterm/i);
  });

  it("holds the lean map back until it knows whether results are on", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3, NY: -10 }, districts: {}, cycleYear: 2026 });
    let answer: (v: unknown) => void = () => {};
    fetchLiveResults.mockReturnValue(new Promise((r) => (answer = r)));
    render(<ElectionsPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/D-LEANING/)).not.toBeInTheDocument();
    answer(CAMPAIGN);
    expect(await screen.findByText(/D-LEANING/)).toBeInTheDocument();
  });

  it("says so when it couldn't check for results, and shows the ballot page", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(<ElectionsPage />);
    expect(await screen.findByText(/COULDN'T CHECK FOR LIVE RESULTS/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("2026 midterm ballot");
  });

  it("draws a covered state whose feed failed as unread, never as no votes yet", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      races: [],
      updates: [],
      feeds: { GA: { status: "unavailable", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: null } },
    });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("FEED NOT READ");
    expect(ga).toHaveTextContent("Senate: couldn't read its feed");
    expect(ga).not.toHaveTextContent(/no votes yet|LIVE/);
    // Drawn with the feed-not-read texture, and said in its name: the dark
    // count-less fills are not told apart by colour alone.
    expect(mapFill.current?.("GA")).toMatch(/^url\(#tex-.*-feed\)$/);
    expect(mapFill.label?.("GA")).toBe("GA: results feed not read");
    expect(screen.getByText(/FEED NOT READ$/, { selector: "li" })).toBeInTheDocument();
  });

  it("marks a state stale when its latest read failed but an older count is shown", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds: {
        GA: {
          status: "stale",
          checkedAt: "2026-11-04T03:00:00Z",
          lastOkAt: "2026-11-04T02:44:00Z",
        },
      },
    });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("STALE");
    expect(ga).toHaveTextContent("LATEST READ FAILED · COUNT FROM NOV 3, 9:44 PM ET");
    expect(ga).toHaveTextContent("Ray Jones (R) leads");
  });

  it("says a tied Senate count is tied, not that the first-listed candidate leads", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    const tie = {
      ...RESULTS.races[0],
      leaderParty: null,
      flip: false,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 950, pct: 50, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 950, pct: 50, candidateId: null },
      ],
    };
    fetchLiveResults.mockResolvedValue({ ...RESULTS, races: [tie], updates: [] });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("Senate: tied");
    expect(ga).not.toHaveTextContent(/leads/);
  });

  it("says election day, not results or polls open, before any covered state's last close", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T20:00:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      phase: { ...RESULTS.phase, phase: "election_day", lastResultChange: null },
      pollsClose: { GA: "2026-11-04T00:00:00Z" },
      feeds: { GA: { status: "polls_open", checkedAt: "2026-11-03T19:55:00Z", lastOkAt: null } },
      races: [],
      updates: [],
    });
    render(<ElectionsPage />);
    const h1 = await screen.findByRole("heading", { level: 1 });
    // The phase starts at midnight Eastern, before any poll opens: "polls
    // are open" would be false for hours.
    expect(h1).toHaveTextContent("2026 midterms: election day");
    expect(h1).not.toHaveTextContent(/results|polls are open/);
    expect(statusRegion()).toHaveTextContent(/^ELECTION DAY$/);
    expect(statusLine()).toHaveTextContent(
      "ELECTION DAY · FIRST STATE'S COUNT SHOWN AFTER NOV 3, 7:00 PM ET"
    );
    expect(
      screen.getByText(/Counts appear here as each state.s last polls close/)
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/Voting is under way|POLLS OPEN/);
    // The state itself: polls not closed, and nothing about its count.
    const ga = within(screen.getByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("POLLS NOT CLOSED");
    expect(ga).toHaveTextContent("Senate: polls not yet closed");
    expect(ga).not.toHaveTextContent(/LIVE|no votes yet/);
    expect(mapFill.current?.("GA")).toMatch(/^url\(#tex-.*-polls\)$/);
    expect(mapFill.label?.("GA")).toBe("GA: polls not yet closed");
    expect(screen.getByText(/POLLS NOT YET CLOSED$/, { selector: "li" })).toBeInTheDocument();
  });

  it("names a leader with no party as other, keys purple on the Senate map, and counts other leads", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    const ind = {
      ...RESULTS.races[0],
      raceId: "2026-SEN-GA",
      leaderParty: null,
      flip: false,
      candidates: [
        { name: "Dan Osgood", party: null, votes: 1000, pct: 52.6, candidateId: null },
        { name: "Pete Rickard", party: "REP", votes: 900, pct: 47.4, candidateId: null },
      ],
    };
    const house = (district: number, leaderParty: string) => ({
      ...RESULTS.races[0],
      raceId: `2026-HOUSE-GA-${district}`,
      office: "H",
      district,
      flip: false,
      leaderParty,
    });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      liveStates: ["GA", "NY"],
      races: [ind, house(1, "REP"), house(2, "IND"), { ...house(3, "REP"), votesCounted: 0 }],
      updates: [],
    });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("Senate: Dan Osgood (party not given) leads");
    expect(ga).not.toHaveTextContent("()");
    expect(ga).toHaveTextContent("HOUSE D 0 · R 1 · I 1 LEADING · NOT FINAL");
    // The Senate map's key has the purple an independent's lead is drawn in.
    expect(screen.getByRole("button", { name: "SENATE" })).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByText(/OTHER OR UNSTATED PARTY LEADS$/, { selector: "li" })
    ).toBeInTheDocument();
    expect(mapFill.current?.("GA")).toContain("var(--map-ind)");
    // Districts with a count so far — not every district "read live".
    const totals = screen.getByRole("region", { name: "Totals" });
    expect(totals).toHaveTextContent(
      "2 districts with a count so far, in 1 state, of 2 states read live"
    );
    expect(totals).not.toHaveTextContent(/districts in \d+ states read live/);
  });

  it("points the flip count at the rule rather than understating it", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue(RESULTS);
    render(<ElectionsPage />);
    const totals = await screen.findByRole("region", { name: "Totals" });
    expect(totals).not.toHaveTextContent(/half or more/);
    expect(within(totals).getByRole("link", { name: "what counts as enough" })).toHaveAttribute(
      "href",
      "/about/elections#election-night"
    );
  });

  it("switches to the count by itself when results start while the page is open", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValueOnce(CAMPAIGN).mockResolvedValue(RESULTS);
    render(<ElectionsPage />);
    expect(await screen.findByText(/R-LEANING/)).toBeInTheDocument();
    // Near election day the campaign page keeps asking; a tab shown again
    // asks at once rather than waiting out the ten minutes.
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "2026 midterm results"
    );
  });

  it("keeps one status line through the switch, changing only its words", async () => {
    // A live region inserted as the mode switches is usually not announced —
    // and that switch is the moment worth announcing.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValueOnce(CAMPAIGN).mockResolvedValue(RESULTS);
    render(<ElectionsPage />);
    await screen.findByText(/R-LEANING/);
    const status = document.querySelector("header [role=status]");
    expect(status).not.toBeNull();
    expect(status).toHaveTextContent("");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await screen.findByText("2026 midterm results");
    expect(document.querySelectorAll("header [role=status]")).toHaveLength(1);
    expect(document.querySelector("header [role=status]")).toBe(status);
    // The region holds the state alone; the time is beside it.
    expect(status).toHaveTextContent(/^LIVE$/);
  });

  it("doesn't re-announce the live region when only the time of the last change moves", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T02:45:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValueOnce(RESULTS).mockResolvedValue({
      ...RESULTS,
      phase: { ...RESULTS.phase, lastResultChange: "2026-11-04T02:47:00Z" },
    });
    render(<ElectionsPage />);
    await screen.findByText("2026 midterm results");
    const region = statusRegion();
    const before = region.innerHTML;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await screen.findByText(/LAST CHANGE NOV 3, 9:47 PM ET/)).toBeInTheDocument();
    expect(statusRegion()).toBe(region);
    expect(region.innerHTML).toBe(before);
  });

  it("says no state is read live, not that it is waiting for counts, with no live state", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T06:00:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      phase: { ...RESULTS.phase, lastResultChange: null },
      liveStates: [],
      races: [],
      updates: [],
    });
    render(<ElectionsPage />);
    await screen.findByText("2026 midterm results");
    expect(statusRegion()).toHaveTextContent("NO STATE'S COUNT IS READ LIVE HERE");
    expect(document.body).not.toHaveTextContent(/WAITING FOR FIRST COUNTS|LIVE ·/);
  });

  it("doesn't say polls are open all day when no state is read live", async () => {
    // `every` over an empty list is true: with no live state the masthead
    // said "polls are open" until the phase ended.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T06:00:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      phase: { ...RESULTS.phase, phase: "election_day", lastResultChange: null },
      liveStates: [],
      pollsClose: {},
      races: [],
      updates: [],
    });
    render(<ElectionsPage />);
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("2026 midterm results");
    expect(h1).not.toHaveTextContent(/polls are open/);
  });

  it("lists both Senate races of a state electing two senators, and says early under half in", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    const regular = { ...RESULTS.races[0], reportingUnits: 500 };
    const special = {
      ...RESULTS.races[0],
      raceId: "2026-SEN-GA-SPECIAL",
      isSpecial: true,
      leaderParty: "DEM",
      flip: false,
      candidates: [
        { name: "Dana Smith", party: "DEM", votes: 1000, pct: 52.6, candidateId: null },
        { name: "Ray Jones", party: "REP", votes: 900, pct: 47.4, candidateId: null },
      ],
    };
    fetchLiveResults.mockResolvedValue({ ...RESULTS, races: [regular, special], updates: [] });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("Senate: Ray Jones (R) leads · not final · early · flip");
    expect(ga).toHaveTextContent("Senate (special): Dana Smith (D) leads");
    expect(ga).not.toHaveTextContent("Dana Smith (D) leads · early");
    // Split between the parties: tied/split on the map, both races named.
    expect(mapFill.label?.("GA")).toMatch(
      /regular race Republican leads.*special race Democrat leads/
    );
    // States, said as states: not "N of M Senate races".
    expect(screen.getByRole("region", { name: "Totals" })).toHaveTextContent(
      "Read live in 1 of the 1 state electing a senator"
    );
  });

  it("says when a refresh failed and how old the count on screen is", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValueOnce({
      ...RESULTS,
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
      },
    });
    render(<ElectionsPage />);
    expect(await screen.findByText(/LAST CHANGE/)).toBeInTheDocument();
    expect(statusRegion()).toHaveTextContent(/^LIVE$/);
    // The next refresh fails: the masthead says when, and which count is
    // still on screen — not just that it is retrying.
    fetchLiveResults.mockRejectedValue(new Error("502"));
    vi.setSystemTime(new Date("2026-11-04T03:03:00Z"));
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await screen.findByText(/SHOWING THE COUNT READ/);
    expect(statusRegion()).toHaveTextContent(/^REFRESH FAILED$/);
    expect(statusLine()).toHaveTextContent(
      "REFRESH FAILED · AT NOV 3, 10:03 PM ET · SHOWING THE COUNT READ AT NOV 3, 10:00 PM ET · RETRYING EVERY MINUTE"
    );
    expect(statusLine()).not.toHaveTextContent(/LIVE/);
  });

  it("dates a failed refresh on the server's clock, beside the count's own times", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // The browser is three hours slow; the server's Date says 03:02.
    vi.setSystemTime(new Date("2026-11-04T00:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValueOnce({
      ...RESULTS,
      clock: { serverDate: Date.parse("2026-11-04T03:02:00Z"), receivedAt: Date.now() },
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
      },
    });
    render(<ElectionsPage />);
    await screen.findByText(/LAST CHANGE/);
    fetchLiveResults.mockRejectedValue(new Error("502"));
    vi.setSystemTime(new Date("2026-11-04T00:03:00Z"));
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await screen.findByText(/SHOWING THE COUNT READ/);
    // Not "AT NOV 3, 7:03 PM ET" — three hours before the count it failed
    // to refresh.
    expect(statusLine()).toHaveTextContent(
      "REFRESH FAILED · AT NOV 3, 10:03 PM ET · SHOWING THE COUNT READ AT NOV 3, 10:00 PM ET"
    );
  });

  it("keeps polls that closed on screen closed when a refresh then fails", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    // GA's polls close 30 s after the answer's Date.
    fetchLiveResults.mockResolvedValueOnce({
      ...RESULTS,
      phase: { ...RESULTS.phase, phase: "election_day", lastResultChange: null },
      races: [],
      updates: [],
      pollsClose: { GA: "2026-11-04T03:02:30Z" },
      clock: { serverDate: Date.parse("2026-11-04T03:02:00Z"), receivedAt: Date.now() },
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:02:00Z", lastOkAt: "2026-11-04T03:02:00Z" },
      },
    });
    render(<ElectionsPage />);
    const h1 = await screen.findByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("2026 midterms: election day");
    expect(statusRegion()).toHaveTextContent(/^ELECTION DAY$/);
    // 45 s on, the refresh fails: the clock stays at 03:02:45, where it
    // stood — past GA's close — not back at 03:02:00, which re-opened them.
    fetchLiveResults.mockRejectedValue(new Error("502"));
    vi.setSystemTime(new Date("2026-11-04T03:02:45Z"));
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(statusRegion()).toHaveTextContent(/^REFRESH FAILED$/));
    expect(statusLine()).not.toHaveTextContent(/ELECTION DAY|FIRST STATE/);
    expect(h1).not.toHaveTextContent("election day");
  });

  it("while its own refreshes fail, says only that — never newly calls a feed stale", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValueOnce({
      ...RESULTS,
      clock: { serverDate: Date.parse("2026-11-04T03:02:00Z"), receivedAt: Date.now() },
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
      },
    });
    render(<ElectionsPage />);
    await screen.findByText(/LAST CHANGE/);
    fetchLiveResults.mockRejectedValue(new Error("502"));
    // Half an hour of failed refreshes: by the browser's clock the feed
    // would be "behind", but that is this page's failure, not the feed's.
    vi.setSystemTime(new Date("2026-11-04T03:32:00Z"));
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await screen.findByText(/SHOWING THE COUNT READ/);
    expect(statusRegion()).toHaveTextContent(/^REFRESH FAILED$/);
    const ga = within(screen.getByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).not.toHaveTextContent(/STALE|NOT CHECKED/);
  });

  it("judges staleness by the server's clock, not a browser clock that is off", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    const feeds = {
      GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
    };
    vi.useFakeTimers({ toFake: ["Date"] });
    // The browser is 40 minutes fast; the server's Date says 03:02.
    vi.setSystemTime(new Date("2026-11-04T03:42:00Z"));
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds,
      clock: { serverDate: Date.parse("2026-11-04T03:02:00Z"), receivedAt: Date.now() },
    });
    render(<ElectionsPage />);
    await screen.findByText(/LAST CHANGE/);
    expect(statusRegion()).toHaveTextContent(/^LIVE$/);
    cleanup();
    // The server's own Date 25 minutes after the read: stale, whatever a
    // slow browser clock says.
    vi.setSystemTime(new Date("2026-11-04T01:00:00Z"));
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds,
      clock: { serverDate: Date.parse("2026-11-04T03:25:00Z"), receivedAt: Date.now() },
    });
    render(<ElectionsPage />);
    await screen.findByText(/SHOWING THE COUNT READ/);
    expect(statusRegion()).toHaveTextContent("STALE · NO STATE'S FEED CHECKED LATELY");
  });

  it("calls a live state with no read record since its polls closed stale, not live", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:20:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      pollsClose: { GA: "2026-11-04T00:00:00Z" },
      feeds: {},
    });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("STALE");
    // With no record, when the count was read is the race's own read time.
    expect(ga).toHaveTextContent(
      "NOT CHECKED SINCE ITS POLLS CLOSED · COUNT FROM NOV 3, 9:44 PM ET"
    );
    expect(statusRegion()).toHaveTextContent(/^STALE/);
  });

  it("marks a state STALE, not LIVE, once the backend stops reading its feed", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // Counts still moving (last change 02:42), so the sync reads every five
    // minutes: a read 20 minutes old is a stopped sync.
    vi.setSystemTime(new Date("2026-11-04T03:20:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      liveStates: ["GA", "NY"],
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
        NY: { status: "ok", checkedAt: "2026-11-04T03:18:00Z", lastOkAt: "2026-11-04T03:18:00Z" },
      },
    });
    render(<ElectionsPage />);
    const byState = within(await screen.findByRole("region", { name: /By state/ }));
    const ga = byState.getByRole("link", { name: /^GA/ });
    expect(ga).toHaveTextContent("STALE");
    expect(ga).not.toHaveTextContent(/LIVE/);
    expect(ga).toHaveTextContent(
      "NOT CHECKED SINCE NOV 3, 10:00 PM ET · COUNT FROM NOV 3, 10:00 PM ET"
    );
    expect(mapFill.label?.("GA")).toMatch(/; not live, not checked since Nov 3, 10:00 PM ET/);
    // The map keeps the last leader's colour under the stale stripe, keyed.
    expect(mapFill.current?.("GA")).toMatch(/^url\(#tex-.*-stale-0\)$/);
    expect(screen.getByText(/STALE: NOT REFRESHED$/, { selector: "li" })).toBeInTheDocument();
    // Not LIVE's badge with other words: its own look.
    const badge = within(ga).getByText("STALE");
    expect(badge.className).toMatch(/border-dashed/);
    expect(
      within(byState.getByRole("link", { name: /^NY/ })).getByText("LIVE").className
    ).not.toMatch(/border-dashed/);
    // A state still read on time stays live, and so does the masthead.
    expect(byState.getByRole("link", { name: /^NY/ })).toHaveTextContent("LIVE");
    expect(statusRegion()).toHaveTextContent(/^LIVE$/);
  });

  it("says STALE in the masthead, with the count's read time, when no feed is being read", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:20:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
      },
    });
    render(<ElectionsPage />);
    await screen.findByText(/SHOWING THE COUNT READ/);
    expect(statusLine()).toHaveTextContent(
      "STALE · NO STATE'S FEED CHECKED LATELY · SHOWING THE COUNT READ AT NOV 3, 10:00 PM ET"
    );
  });

  it("says every read failed, not that none was made, when feeds are read but fail", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:02:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds: {
        GA: {
          status: "unavailable",
          checkedAt: "2026-11-04T03:00:00Z",
          lastOkAt: "2026-11-04T02:44:00Z",
        },
      },
    });
    render(<ElectionsPage />);
    await screen.findByText(/SHOWING THE COUNT READ/);
    expect(statusRegion()).toHaveTextContent("STALE · EVERY STATE'S LATEST FEED READ FAILED");
  });

  it("gives the oldest and newest read when counts were read at different times", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T03:40:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      liveStates: ["GA", "NY"],
      races: [
        RESULTS.races[0],
        {
          ...RESULTS.races[0],
          raceId: "2026-SEN-NY",
          state: "NY",
          fetchedAt: "2026-11-04T03:18:00Z",
        },
      ],
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T03:00:00Z" },
        NY: { status: "ok", checkedAt: "2026-11-04T03:18:00Z", lastOkAt: "2026-11-04T03:18:00Z" },
      },
    });
    render(<ElectionsPage />);
    await screen.findByText(/SHOWING COUNTS READ/);
    expect(statusLine()).toHaveTextContent(
      "SHOWING COUNTS READ BETWEEN NOV 3, 10:00 PM ET AND NOV 3, 10:18 PM ET"
    );
  });

  it("allows the hourly sync its hour once no count has moved for a day", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-06T03:45:00Z"));
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds: {
        GA: { status: "ok", checkedAt: "2026-11-06T03:00:00Z", lastOkAt: "2026-11-06T03:00:00Z" },
      },
    });
    render(<ElectionsPage />);
    const ga = within(await screen.findByRole("region", { name: /By state/ })).getByRole("link", {
      name: /^GA/,
    });
    expect(ga).toHaveTextContent("LIVE");
    expect(ga).not.toHaveTextContent(/STALE/);
  });

  it("says how often it is really retrying", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(<ElectionsPage />);
    expect(await screen.findByText(/RETRYING EVERY MINUTE/)).toBeInTheDocument();
  });
});
