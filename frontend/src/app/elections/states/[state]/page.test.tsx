import { afterEach, describe, expect, it, vi } from "vitest";
import { generateMetadata } from "./page";

/* A state page's search and link-card wording turns to "Election Results"
   when its h1 does: once the state's polls have closed (pollsClosed), never
   from midnight on election day. */

vi.mock("./StateBallotClient", () => ({ default: () => null }));

const DAY = {
  phase: "election_day",
  electionDate: "2026-11-03",
  resultsUntil: null,
  lastResultChange: null,
};

const BALLOT = {
  state: "GA",
  stateName: "Georgia",
  cycleYear: 2026,
  electionDate: "2026-11-03",
  senateRaces: [],
  houseRaces: [],
  measures: [],
  phase: DAY,
};

function mockBackend(ballot: unknown, results: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.includes("/api/elections/results") ? results : ballot;
      return { ok: true, status: 200, json: async () => body } as Response;
    })
  );
}

const params = Promise.resolve({ state: "GA" });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("a state page's metadata on election day", () => {
  const results = {
    phase: DAY,
    liveStates: ["GA"],
    senateStates: ["GA"],
    pollsClose: { GA: "2026-11-04T00:00:00Z" },
    races: [],
    updates: [],
  };

  it("says ballot, not results, while the state's polls are open", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T13:00:00Z"));
    mockBackend(BALLOT, results);
    const meta = await generateMetadata({ params });
    expect(meta.title).toBe("Georgia Ballot 2026: Senate, House Races & Ballot Measures");
    expect(JSON.stringify(meta)).not.toMatch(/Results|leading until official/);
  });

  it("says results once the state's last polls have closed", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T00:05:00Z"));
    mockBackend(BALLOT, results);
    const meta = await generateMetadata({ params });
    expect(meta.title).toBe("Georgia Election Results 2026: Senate & House Count and Ballot");
    expect(meta.description).toMatch(/leading until official/);
  });

  it("treats an unknown close as not closed, and the day after as closed everywhere", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T00:05:00Z"));
    // A state with no live feed: no closing time is sent for it, as the page's
    // own heading reads it — still the ballot page.
    mockBackend(BALLOT, { ...results, liveStates: ["WA"], pollsClose: {} });
    expect((await generateMetadata({ params })).title).toMatch(/^Georgia Ballot 2026/);
    const after = { ...DAY, phase: "results" };
    mockBackend({ ...BALLOT, phase: after }, { ...results, phase: after, liveStates: ["WA"] });
    expect((await generateMetadata({ params })).title).toMatch(/^Georgia Election Results 2026/);
  });
});
