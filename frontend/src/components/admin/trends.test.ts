import { describe, expect, it } from "vitest";
import type { PipelineTrendRun } from "@/lib/api";
import { actionRunsOldestFirst, dayWindow, durationPoints, runsPerDay } from "./trends";

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
