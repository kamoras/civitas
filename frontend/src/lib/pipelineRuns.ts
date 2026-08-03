/**
 * How a pipeline run is presented in the admin run-history table.
 *
 * This exists as its own module, keyed off an exhaustive
 * `Record<PipelineType, …>`, because the previous shape was a chain of
 * negations:
 *
 *     const isSenate = !isHouse && !isStockTrades && !isSupplementary;
 *
 * "Senate" was whatever hadn't been explicitly excluded, so the Election
 * pipeline — added later — rendered as SENATE rows showing `undefined/
 * undefined` processed counts. Patching that chain with one more `&&
 * !isElection` would have left the sixth pipeline type to hit the same bug.
 *
 * With the record below, adding a `PipelineType` without a descriptor is a
 * TypeScript error, so the failure moves from "silently mislabeled in
 * production" to "does not compile".
 */

import type { PipelineHistoryRun, PipelineType } from "@/lib/api";

export interface RunDisplay {
  /** Short label for the TYPE column. */
  label: string;
  /**
   * Only the Senate pipeline records LLM-call and cache counters, so every
   * other type renders those columns as "—". This drives the TYPE column's
   * colour too: Senate is the routine, muted case.
   */
  hasLlmStats: boolean;
  /** The PROCESSED column's main text, e.g. "42/100" or "3H/1S/0P". */
  processed: string;
  /** Failure count to call out next to `processed`; 0 when the type has none. */
  failed: number;
}

const n = (value: number | undefined) => value ?? 0;

interface RunDescriptor {
  label: string;
  hasLlmStats?: boolean;
  processed: (run: PipelineHistoryRun) => string;
  failed?: (run: PipelineHistoryRun) => number;
}

const DESCRIPTORS: Record<PipelineType, RunDescriptor> = {
  senate: {
    label: "SENATE",
    hasLlmStats: true,
    processed: (r) => `${n(r.senatorsProcessed)}/${n(r.senatorsTotal)}`,
    failed: (r) => n(r.senatorsFailed),
  },
  house: {
    label: "HOUSE",
    processed: (r) => `${n(r.repsProcessed)}/${n(r.repsTotal)}`,
    failed: (r) => n(r.repsFailed),
  },
  stock_trades: {
    label: "STOCK",
    processed: (r) =>
      `${n(r.houseTradesIngested)}H/${n(r.senateTradesIngested)}S/${n(r.presidentTradesIngested)}P`,
  },
  supplementary: {
    label: "SUPP",
    // An explicitly skipped justice phase is "—", not "0" — the two mean
    // different things and the dash is the existing convention here.
    processed: (r) =>
      `${n(r.presidentsUpdated)}P/${r.justicesSkipped ? "—" : n(r.justicesScored)}J`,
  },
  election: {
    label: "ELECTION",
    processed: (r) =>
      `${n(r.candidatesSynced)}C/${n(r.financialsRefreshed)}F/${n(r.coverageItemsIngested)}N`,
  },
};

/**
 * Resolve a history row's pipeline type.
 *
 * `pipelineType` is optional on the wire, and rows that predate the field
 * are Senate runs — that is what the run-history key fallback has always
 * assumed. An *unrecognised* type is a different case and deliberately not
 * treated as Senate: that is the bug this module exists to prevent, so a
 * type this build doesn't know about is surfaced as itself instead.
 */
export function pipelineTypeOf(run: PipelineHistoryRun): PipelineType | null {
  if (run.pipelineType === undefined) return "senate";
  return run.pipelineType in DESCRIPTORS ? run.pipelineType : null;
}

export function describeRun(run: PipelineHistoryRun): RunDisplay {
  const type = pipelineTypeOf(run);
  if (type === null) {
    // A newer backend grew a pipeline type this frontend hasn't been
    // rebuilt for. Show the raw type rather than a wrong one; the counters
    // for it are unknown, so claim nothing about them.
    return {
      label: String(run.pipelineType).toUpperCase(),
      hasLlmStats: false,
      processed: "—",
      failed: 0,
    };
  }
  const descriptor = DESCRIPTORS[type];
  return {
    label: descriptor.label,
    hasLlmStats: descriptor.hasLlmStats ?? false,
    processed: descriptor.processed(run),
    failed: descriptor.failed?.(run) ?? 0,
  };
}

/** Cache hit rate as a whole-number percentage, or null when there is no data. */
export function cacheHitRate(run: PipelineHistoryRun): number | null {
  const hits = n(run.cacheHits);
  const total = hits + n(run.cacheMisses);
  if (total <= 0) return null;
  return Math.round((hits / total) * 100);
}
