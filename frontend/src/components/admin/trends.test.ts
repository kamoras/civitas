import { describe, expect, it } from "vitest";
import type { PipelineTrendRun } from "@/lib/api";
import {
  actionRunsOldestFirst,
  dayWindow,
  durationPoints,
  finishedSince,
  historyPreview,
  hourlySlots,
  runsPerDay,
  slotSum,
  slotStates,
} from "./trends";

const run = (over: Partial<PipelineTrendRun>): PipelineTrendRun => ({
  id: 1,
  pipelineType: "senate",
  startedAt: "2026-09-25T03:00:00",
  status: "completed",
  elapsedSeconds: 60,
  ...over,
});

describe("dayWindow", () => {
  it("lists calendar days oldest first, ending today", () => {
    expect(dayWindow(3, "2026-03-01")).toEqual(["2026-02-27", "2026-02-28", "2026-03-01"]);
  });
});

describe("runsPerDay", () => {
  const dates = dayWindow(3, "2026-09-26");

  it("buckets runs by their UTC start date and outcome", () => {
    // 2026-09-25T23:30 UTC is still the 25th, although it is the 25th at
    // 16:30 in the suite's Los Angeles zone and the 26th in UTC+1.
    const out = runsPerDay(
      [
        run({ startedAt: "2026-09-25T23:30:00" }),
        run({ startedAt: "2026-09-25T04:00:00", status: "failed" }),
        run({ startedAt: "2026-09-26T04:00:00", status: "partial" }),
      ],
      dates
    );
    expect(out.completed).toEqual([0, 1, 0]);
    expect(out.failed).toEqual([0, 1, 0]);
    expect(out.partial).toEqual([0, 0, 1]);
  });

  it("does not count a run that is still running, or one outside the window", () => {
    const out = runsPerDay(
      [run({ status: "running" }), run({ startedAt: "2026-08-01T00:00:00" })],
      dates
    );
    expect(out.completed).toEqual([0, 0, 0]);
  });
});

describe("durationPoints", () => {
  it("keeps finished runs of one pipeline, failed ones included", () => {
    const points = durationPoints(
      [
        run({ id: 1, elapsedSeconds: 100 }),
        run({ id: 2, status: "failed", elapsedSeconds: 12 }),
        run({ id: 3, status: "running", elapsedSeconds: null }),
        run({ id: 4, pipelineType: "house" }),
      ],
      "senate"
    );
    expect(points.map((p) => [p.seconds, p.status])).toEqual([
      [100, "completed"],
      [12, "failed"],
    ]);
  });
});

describe("actionRunsOldestFirst", () => {
  it("reverses the API's newest-first order without mutating it", () => {
    const runs = [
      {
        run: "b",
        recordedAt: "2026-09-26T02:00:00",
        counts: {},
        issuesPublished: 0,
        suppressed: 0,
      },
      {
        run: "a",
        recordedAt: "2026-09-26T01:00:00",
        counts: {},
        issuesPublished: 0,
        suppressed: 0,
      },
    ];
    expect(actionRunsOldestFirst(runs).map((r) => r.run)).toEqual(["a", "b"]);
    expect(runs[0].run).toBe("b");
  });
});

describe("hourlySlots", () => {
  const now = Date.parse("2026-09-26T12:30:00Z");
  const r = (at: string, n = 0) => ({
    run: at,
    recordedAt: at,
    counts: {},
    issuesPublished: n,
    suppressed: 0,
  });

  it("slots on the :15 refresh grid, so a slow run stays in the slot it started in", () => {
    // 09:15 ends 09:50; 10:15 is slow and ends 11:05; 11:15 is skipped
    // (10:15's still running); 12:15 ends 12:20.
    const slots = hourlySlots(
      [r("2026-09-26T09:50:00"), r("2026-09-26T11:05:00"), r("2026-09-26T12:20:00")],
      4,
      now
    );
    expect(slots.map((s) => new Date(s.start).toISOString().slice(11, 16))).toEqual([
      "09:15",
      "10:15",
      "11:15",
      "12:15",
    ]);
    expect(slots.map((s) => s.runs.length)).toEqual([1, 1, 0, 1]);
  });

  it("drops runs outside the window and keeps both runs of a doubled slot", () => {
    const slots = hourlySlots(
      [r("2026-09-26T12:20:00", 2), r("2026-09-26T12:25:00", 1), r("2026-09-25T01:00:00")],
      2,
      now
    );
    expect(slotSum(slots[0], (x) => x.issuesPublished)).toBeNull();
    expect(slotSum(slots[1], (x) => x.issuesPublished)).toBe(3);
  });
});

describe("historyPreview", () => {
  it("keeps the newest runs of every pipeline, so a weekly one is never crowded out", () => {
    const rows = [
      ...Array.from({ length: 30 }, (_, i) => ({ pipelineType: "senate", startedAt: `s${i}` })),
      { pipelineType: "stock_trades", startedAt: "old-stock" },
    ];
    const preview = historyPreview(rows, 5);
    expect(preview.filter((r) => r.pipelineType === "senate")).toHaveLength(5);
    expect(preview.map((r) => r.startedAt)).toContain("old-stock");
  });
});

describe("finishedSince", () => {
  const p = (key: string, running: boolean, status: string) => ({
    key,
    label: key.toUpperCase(),
    running,
    status,
    elapsedSeconds: 10,
  });

  it("reports every pipeline that stopped, and partial as partial", () => {
    const out = finishedSince({ house: true, election: true, senate: false }, [
      p("senate", false, "completed"),
      p("house", false, "partial"),
      p("election", false, "failed"),
    ]);
    expect(out.map((f) => [f.key, f.status])).toEqual([
      ["house", "partial"],
      ["election", "failed"],
    ]);
  });

  it("reports nothing on the first poll or while still running", () => {
    expect(finishedSince({}, [p("senate", false, "completed")])).toEqual([]);
    expect(finishedSince({ senate: true }, [p("senate", true, "running")])).toEqual([]);
  });
});

describe("slotStates", () => {
  const now = Date.parse("2026-09-26T08:30:00Z");
  const r = (at: string) => ({
    run: at,
    recordedAt: at,
    counts: {},
    issuesPublished: 0,
    suppressed: 0,
  });
  const idle = { runningNow: {}, processStartedAt: null, refreshStartedAt: null };
  // Slots 03:15 .. 08:15; only 06:15 has a run.
  const slots = hourlySlots([r("2026-09-26T06:20:00")], 6, now);

  it("tells a deliberate pipeline skip from a crash, and the current slot from either", () => {
    const senate = run({
      pipelineType: "senate",
      startedAt: "2026-09-26T03:00:00",
      elapsedSeconds: 7200,
    });
    expect(slotStates(slots, [senate], now, idle)).toEqual([
      "skipped",
      "skipped",
      "missing",
      "ran",
      "missing",
      "pending",
    ]);
  });

  it("treats every pipeline the scheduler waits on as blocking, and election as not", () => {
    const at = (pipelineType: string) =>
      run({ pipelineType, startedAt: "2026-09-26T07:00:00", elapsedSeconds: 1800 });
    for (const type of ["senate", "house", "supplementary", "stock_trades"]) {
      expect(slotStates(slots, [at(type)], now, idle)[4]).toBe("skipped");
    }
    expect(slotStates(slots, [at("election")], now, idle)[4]).toBe("missing");
  });

  it("stops blocking at the scheduler's stale limit", () => {
    // Stock trades is honoured for 2h: ticks at 03:15 and 04:15 are skips,
    // 05:15 onward the scheduler would have refreshed anyway.
    const stock = run({
      pipelineType: "stock_trades",
      startedAt: "2026-09-26T03:00:00",
      elapsedSeconds: 5 * 3600,
    });
    expect(slotStates(slots, [stock], now, idle).slice(0, 3)).toEqual([
      "skipped",
      "skipped",
      "missing",
    ]);
  });

  it("ends a row orphaned by a restart at the restart, not now", () => {
    const orphan = run({
      pipelineType: "house",
      startedAt: "2026-09-26T03:00:00",
      status: "running",
      // Real running rows carry the last progress flush, not null.
      elapsedSeconds: 600,
    });
    const ctx = { ...idle, processStartedAt: Date.parse("2026-09-26T04:30:00Z") };
    expect(slotStates(slots, [orphan], now, ctx).slice(0, 3)).toEqual([
      "skipped",
      "skipped",
      "missing",
    ]);
    // Still genuinely running (flag set): blocks up to now.
    const live = { ...ctx, runningNow: { house: true } };
    expect(slotStates(slots, [orphan], now, live)[4]).toBe("skipped");
    // Flag cleared with no restart since the run began: it just finished
    // after the rows were fetched — not an orphan, so its ticks stay skipped.
    const justFinished = { ...idle, processStartedAt: Date.parse("2026-09-20T00:00:00Z") };
    expect(slotStates(slots, [orphan], now, justFinished).slice(0, 2)).toEqual([
      "skipped",
      "skipped",
    ]);
  });

  it("marks a slow refresh still in flight as pending, and the ticks it blocks as skipped", () => {
    const ctx = { ...idle, refreshStartedAt: Date.parse("2026-09-26T07:15:00Z") };
    const early = hourlySlots([], 6, now);
    const states = slotStates(early, [], now, ctx);
    expect(states[4]).toBe("pending"); // 07:15, still running
    expect(states[5]).toBe("pending"); // 08:15, current slot
    expect(states[3]).toBe("missing"); // 06:15, before it started
  });
});
