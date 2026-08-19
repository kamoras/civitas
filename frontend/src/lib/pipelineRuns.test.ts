import { describe, expect, it } from "vitest";

import type { PipelineHistoryRun, PipelineType } from "@/lib/api";
import { cacheHitRate, describeRun, pipelineTypeOf } from "@/lib/pipelineRuns";

function run(overrides: Partial<PipelineHistoryRun> = {}): PipelineHistoryRun {
  return {
    id: 1,
    startedAt: "2026-08-02T10:00:00Z",
    completedAt: "2026-08-02T10:05:00Z",
    status: "completed",
    elapsedSeconds: 300,
    errorMessage: null,
    ...overrides,
  };
}

const ALL_TYPES: PipelineType[] = ["senate", "house", "stock_trades", "supplementary", "election"];

describe("describeRun — labels", () => {
  it.each([
    ["senate", "SENATE"],
    ["house", "HOUSE"],
    ["stock_trades", "STOCK"],
    ["supplementary", "SUPP"],
    ["election", "ELECTION"],
  ] as const)("labels a %s run as %s", (pipelineType, label) => {
    expect(describeRun(run({ pipelineType })).label).toBe(label);
  });

  it("never labels a non-senate run as SENATE", () => {
    // The regression itself: Election runs rendered as SENATE because the
    // old code defined "senate" as "none of the other four".
    for (const pipelineType of ALL_TYPES.filter((t) => t !== "senate")) {
      expect(describeRun(run({ pipelineType })).label).not.toBe("SENATE");
    }
  });

  it("treats a row with no pipelineType as senate", () => {
    // Rows written before the field existed are Senate runs.
    expect(describeRun(run()).label).toBe("SENATE");
    expect(pipelineTypeOf(run())).toBe("senate");
  });

  it("surfaces an unrecognised type as itself rather than as senate", () => {
    // A backend that grows a sixth pipeline before this build knows about
    // it must not be silently mislabeled — that is the whole bug.
    const unknown = run({ pipelineType: "judicial" as PipelineType });
    expect(pipelineTypeOf(unknown)).toBeNull();
    expect(describeRun(unknown).label).toBe("JUDICIAL");
    expect(describeRun(unknown).processed).toBe("—");
    expect(describeRun(unknown).hasLlmStats).toBe(false);
  });
});

describe("describeRun — processed counts", () => {
  it("renders senate counts and failures", () => {
    const d = describeRun(
      run({
        pipelineType: "senate",
        senatorsProcessed: 98,
        senatorsTotal: 100,
        senatorsFailed: 2,
      })
    );
    expect(d.processed).toBe("98/100");
    expect(d.failed).toBe(2);
  });

  it("renders house counts and failures", () => {
    const d = describeRun(
      run({ pipelineType: "house", repsProcessed: 430, repsTotal: 435, repsFailed: 5 })
    );
    expect(d.processed).toBe("430/435");
    expect(d.failed).toBe(5);
  });

  it("renders stock trade counts per chamber", () => {
    const d = describeRun(
      run({
        pipelineType: "stock_trades",
        houseTradesIngested: 12,
        senateTradesIngested: 3,
        presidentTradesIngested: 1,
      })
    );
    expect(d.processed).toBe("12H/3S/1P");
    expect(d.failed).toBe(0);
  });

  it("renders supplementary counts, distinguishing a skipped justice phase from zero", () => {
    expect(
      describeRun(run({ pipelineType: "supplementary", presidentsUpdated: 4, justicesScored: 9 }))
        .processed
    ).toBe("4P/9J");
    expect(
      describeRun(
        run({
          pipelineType: "supplementary",
          presidentsUpdated: 4,
          justicesScored: 0,
          justicesSkipped: true,
        })
      ).processed
    ).toBe("4P/—J");
  });

  it("renders election counts — candidates, financials, coverage", () => {
    const d = describeRun(
      run({
        pipelineType: "election",
        candidatesSynced: 120,
        financialsRefreshed: 87,
        coverageItemsIngested: 40,
      })
    );
    expect(d.processed).toBe("120C/87F/40N");
    expect(d.failed).toBe(0);
  });

  it("renders zeros, never 'undefined', when a run carries no counters", () => {
    // The visible symptom of the bug was an Election row rendering
    // "undefined/undefined" from the senate branch's fields.
    for (const pipelineType of ALL_TYPES) {
      const { processed } = describeRun(run({ pipelineType }));
      expect(processed).not.toContain("undefined");
      expect(processed).not.toContain("NaN");
    }
  });
});

describe("describeRun — LLM/cache columns", () => {
  it("marks only senate runs as carrying LLM stats", () => {
    for (const pipelineType of ALL_TYPES) {
      expect(describeRun(run({ pipelineType })).hasLlmStats).toBe(pipelineType === "senate");
    }
  });
});

describe("cacheHitRate", () => {
  it("computes a whole-number percentage", () => {
    expect(cacheHitRate(run({ cacheHits: 75, cacheMisses: 25 }))).toBe(75);
    expect(cacheHitRate(run({ cacheHits: 1, cacheMisses: 2 }))).toBe(33);
  });

  it("returns null rather than NaN when a run has no cache counters", () => {
    // Non-senate history rows genuinely have neither field; `hits + misses`
    // on them used to be NaN.
    expect(cacheHitRate(run({ pipelineType: "election" }))).toBeNull();
    expect(cacheHitRate(run({ cacheHits: 0, cacheMisses: 0 }))).toBeNull();
  });

  it("handles a run with hits but no misses recorded", () => {
    expect(cacheHitRate(run({ cacheHits: 10 }))).toBe(100);
  });
});
