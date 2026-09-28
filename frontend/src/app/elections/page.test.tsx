import { describe, expect, it, vi } from "vitest";
import { afterEach, beforeEach } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { FEED_FAILED_FILL } from "@/lib/results";
import ElectionsPage from "./page";

const fetchPviMap = vi.hoisted(() => vi.fn());
const fetchLiveResults = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchPviMap, fetchLiveResults }));

const CAMPAIGN = {
  cycleYear: 2026,
  phase: { phase: "campaign", electionDate: "2026-11-03", resultsUntil: null, lastResultChange: null },
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
      raceId: "2026-SEN-GA", state: "GA", office: "S", district: null, isSpecial: false, heldBy: "DEM",
      official: false, votesCounted: 1900, reportingUnits: 2103, totalUnits: 2653, unitLabel: "precincts",
      sourceName: "Georgia Secretary of State", sourceUrl: "https://results.example/ga",
      fetchedAt: "2026-11-04T02:44:00Z", lastChangeAt: "2026-11-04T02:42:00Z", leaderParty: "REP", flip: true,
      candidates: [
        { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6, candidateId: null },
        { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4, candidateId: null },
      ],
    },
  ],
  updates: [
    {
      id: 1, raceId: "2026-SEN-GA", state: "GA", office: "S", district: null, isSpecial: false, kind: "flip",
      at: "2026-11-04T02:42:00Z",
      detail: {
        leader: { name: "Ray Jones", party: "REP", votes: 1000, pct: 52.6 },
        runnerUp: { name: "Dana Smith", party: "DEM", votes: 900, pct: 47.4 },
        reportingUnits: 2103, totalUnits: 2653, unitLabel: "precincts", heldBy: "DEM",
      },
    },
  ],
};
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
// RaceMap pulls in react-simple-maps and a topojson payload; the page's own
// behaviour is what is under test here, not the map's rendering.
const mapFill = vi.hoisted(() => ({ current: null as null | ((s: string) => string) }));
vi.mock("@/components/elections/RaceMap", () => ({
  default: (props: { getFillColor: (s: string) => string }) => {
    mapFill.current = props.getFillColor;
    return <div data-testid="race-map" />;
  },
  FIPS_TO_STATE: { "13": "GA", "36": "NY", "11": "DC" },
}));
vi.mock("@/components/layout/Navbar", () => ({ default: () => <header /> }));
vi.mock("@/components/layout/Footer", () => ({ default: () => <footer /> }));
vi.mock("@/components/BackToTop", () => ({ default: () => null }));

beforeEach(() => {
  fetchLiveResults.mockResolvedValue(CAMPAIGN);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("ElectionsPage", () => {
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

  it("surfaces a fetch failure instead of hanging on the loading line", async () => {
    fetchPviMap.mockRejectedValue(new Error("Failed to load election data"));
    render(<ElectionsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load election data");
  });

  it("leads with the live count from election day on", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue(RESULTS);
    render(<ElectionsPage />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("2026 midterm results");
    expect(screen.getByText(/leads in a seat Democrats hold/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SENATE" })).toHaveAttribute("aria-pressed", "true");
    // The lean key is gone: the map now shades by the count.
    expect(screen.queryByText(/D-LEANING/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/LIVE · LAST CHANGE/);
  });

  it("keeps the campaign page when the backend predates the phase", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockRejectedValue(new Error("404"));
    render(<ElectionsPage />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("2026 midterm ballot");
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
    expect(mapFill.current?.("GA")).toBe(FEED_FAILED_FILL);
    expect(screen.getByText(/FEED NOT READ$/, { selector: "li" })).toBeInTheDocument();
  });

  it("marks a state stale when its latest read failed but an older count is shown", async () => {
    fetchPviMap.mockResolvedValue({ states: {}, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockResolvedValue({
      ...RESULTS,
      feeds: {
        GA: { status: "stale", checkedAt: "2026-11-04T03:00:00Z", lastOkAt: "2026-11-04T02:44:00Z" },
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
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("2026 midterm results");
  });

  it("says how often it is really retrying", async () => {
    fetchPviMap.mockResolvedValue({ states: { GA: 3 }, districts: {}, cycleYear: 2026 });
    fetchLiveResults.mockRejectedValue(new Error("502"));
    render(<ElectionsPage />);
    expect(await screen.findByText(/RETRYING EVERY MINUTE/)).toBeInTheDocument();
  });
});
