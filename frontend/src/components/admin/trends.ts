/**
 * Pure reshaping of admin API responses into chart series. No React, so the
 * bucketing rules (which day a run belongs to, what counts as a failure) are
 * tested directly rather than through a rendered chart.
 */

import type { ActionMetricsRun, PipelineTrendRun } from "@/lib/api";
import { parseUTC } from "./format";

/** The last `days` UTC dates ending at `today` (YYYY-MM-DD), oldest first. */
export function dayWindow(days: number, today: string): string[] {
  const end = Date.parse(`${today}T00:00:00Z`);
  return Array.from({ length: days }, (_, i) =>
    new Date(end - (days - 1 - i) * 86_400_000).toISOString().slice(0, 10)
  );
}

export function utcToday(now: number = Date.now()): string {
  return new Date(now).toISOString().slice(0, 10);
}

/** The UTC date a run started on — the day it is counted against. */
export function runDay(run: { startedAt: string | null }): string | null {
  return run.startedAt ? parseUTC(run.startedAt).toISOString().slice(0, 10) : null;
}

export interface RunsPerDay {
  dates: string[];
  completed: number[];
  partial: number[];
  failed: number[];
}

/**
 * Finished runs per day across every pipeline, split by outcome. A run still
 * "running" is not counted yet: it has no outcome, and counting it as either
 * would have to be taken back when it ends.
 */
export function runsPerDay(runs: PipelineTrendRun[], dates: string[]): RunsPerDay {
  const index = new Map(dates.map((d, i) => [d, i]));
  const out: RunsPerDay = {
    dates,
    completed: dates.map(() => 0),
    partial: dates.map(() => 0),
    failed: dates.map(() => 0),
  };
  for (const run of runs) {
    const day = runDay(run);
    const i = day == null ? undefined : index.get(day);
    if (i === undefined) continue;
    if (run.status === "completed") out.completed[i]++;
    else if (run.status === "partial") out.partial[i]++;
    else if (run.status === "failed") out.failed[i]++;
  }
  return out;
}

/**
 * One point per finished run of `pipelineType`, oldest first, for a
 * duration-over-time chart. Failed runs are kept — a run that died after ten
 * minutes is exactly the dip worth seeing — and flagged so the table and
 * tooltip can say so.
 */
export function durationPoints(
  runs: PipelineTrendRun[],
  pipelineType: string
): { startedAt: string; seconds: number; status: string }[] {
  return runs
    .filter(
      (r) =>
        r.pipelineType === pipelineType &&
        r.startedAt != null &&
        r.elapsedSeconds != null &&
        r.status !== "running"
    )
    .map((r) => ({
      startedAt: r.startedAt as string,
      seconds: r.elapsedSeconds as number,
      status: r.status,
    }));
}

/** Action Center metric runs, oldest first (the API returns newest first). */
export function actionRunsOldestFirst(runs: ActionMetricsRun[]): ActionMetricsRun[] {
  return [...runs].sort((a, b) => (a.recordedAt ?? "").localeCompare(b.recordedAt ?? ""));
}
