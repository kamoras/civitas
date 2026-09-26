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
