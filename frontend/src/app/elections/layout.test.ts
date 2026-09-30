import { afterEach, describe, expect, it, vi } from "vitest";
import { generateMetadata } from "./layout";

/* /elections' search and link-card wording: "Election Results" only once
   the page itself leads with a count — on election day, not until some
   covered state's polls have closed (everyLiveStateVoting). */

const DAY = {
  phase: "election_day",
  electionDate: "2026-11-03",
  resultsUntil: null,
  lastResultChange: null,
};

function mockBackend(results: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.includes("/api/elections/races") ? [{ cycleYear: 2026 }] : results;
      return { ok: true, json: async () => body } as Response;
    })
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("/elections metadata", () => {
  const liveDay = {
    phase: DAY,
    liveStates: ["GA", "WA"],
    senateStates: ["GA"],
    pollsClose: { GA: "2026-11-04T00:00:00Z", WA: "2026-11-04T04:00:00Z" },
    races: [],
    updates: [],
  };

  it("keeps the ballot wording on election day while every covered state is voting", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T13:00:00Z"));
    mockBackend(liveDay);
    const meta = await generateMetadata();
    expect(meta.title).toBe("2026 Elections by State: Senate, House & Ballot Measures");
    expect(JSON.stringify(meta)).not.toMatch(/Results/);
  });

  it("turns to results once the first covered state's polls close", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-04T00:01:00Z"));
    mockBackend(liveDay);
    expect((await generateMetadata()).title).toBe(
      "2026 Election Results by State: Senate & House Count"
    );
  });

  it("is results the day after, and the ballot page when the backend can't be read", async () => {
    mockBackend({ ...liveDay, phase: { ...DAY, phase: "results" } });
    expect((await generateMetadata()).title).toMatch(/Election Results/);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("unreachable");
      })
    );
    expect((await generateMetadata()).title).toBe(
      "Elections by State: Senate, House & Ballot Measures"
    );
  });
});
