import { describe, expect, it } from "vitest";
import { describeRunState, formatRunTimestamp } from "./RecordsBand";
import type { PipelineRunInfo, PipelineStatus } from "@/lib/api";

function run(overrides: Partial<PipelineRunInfo> = {}): PipelineRunInfo {
  return {
    id: 1284,
    startedAt: "2026-08-18T03:00:00Z",
    completedAt: "2026-08-18T03:07:00Z",
    status: "completed",
    currentPhase: null,
    senatorsProcessed: 100,
    senatorsTotal: 100,
    senatorsFailed: 0,
    billsClassified: 0,
    llmCalls: 0,
    cacheHits: 0,
    cacheMisses: 0,
    elapsedSeconds: 420,
    errorMessage: null,
    ...overrides,
  };
}

const status = (o: Partial<PipelineStatus> = {}): PipelineStatus => ({
  lastRun: run(),
  nextScheduled: null,
  isRunning: false,
  ...o,
});

describe("formatRunTimestamp", () => {
  it("renders UTC at minute precision so the band never drifts by locale", () => {
    // The whole value of a "filed at" stamp is that two people comparing
    // notes see the same string; a locale-formatted one does not.
    expect(formatRunTimestamp("2026-08-18T03:07:42Z")).toBe("2026-08-18 03:07 UTC");
  });

  it("pads single-digit months, days, hours and minutes", () => {
    expect(formatRunTimestamp("2026-01-05T04:09:00Z")).toBe("2026-01-05 04:09 UTC");
  });

  it("returns null for an unparseable timestamp rather than 'NaN-NaN-NaN'", () => {
    expect(formatRunTimestamp("not a date")).toBeNull();
  });
});

describe("describeRunState", () => {
  it("reports COMPLETE only for a run that actually finished", () => {
    expect(describeRunState(status()).label).toBe("COMPLETE");
  });

  it("does not dress a failed run as fresh data", () => {
    // A stale record has to be visible rather than assumed — that is the
    // entire reason this band exists.
    expect(describeRunState(status({ lastRun: run({ status: "failed" }) })).label).toBe(
      "LAST RUN FAILED"
    );
    expect(describeRunState(status({ lastRun: run({ errorMessage: "boom" }) })).label).toBe(
      "LAST RUN FAILED"
    );
  });

  it("distinguishes a run still going from one that never finished", () => {
    expect(describeRunState(status({ isRunning: true })).label).toBe("RUN IN PROGRESS");
    expect(describeRunState(status({ lastRun: run({ completedAt: null }) })).label).toBe(
      "RUN INCOMPLETE"
    );
  });

  it("handles an unreachable endpoint and an empty history separately", () => {
    expect(describeRunState(null).label).toBe("STATUS UNAVAILABLE");
    expect(describeRunState(status({ lastRun: null })).label).toBe("NO RUN RECORDED");
  });

  it("never colours a non-complete state with the phosphor accent", () => {
    // Phosphor is the 'good' signal in this palette; using it for a failure
    // would make a broken pipeline read as a healthy one at a glance.
    for (const s of [
      null,
      status({ isRunning: true }),
      status({ lastRun: null }),
      status({ lastRun: run({ status: "failed" }) }),
      status({ lastRun: run({ completedAt: null }) }),
    ]) {
      expect(describeRunState(s).tone).not.toBe("text-phos");
    }
  });
});
