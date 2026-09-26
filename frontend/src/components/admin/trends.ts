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

export interface HourSlot {
  /** Start of the slot, ms since epoch: HH:15 UTC-aligned (the refresh grid). */
  start: number;
  /** Runs recorded in this slot — normally one, two after a manual refresh. */
  runs: ActionMetricsRun[];
}

/** Minutes past the hour the scheduler starts a refresh (scheduler.py). */
export const REFRESH_MINUTE = 15;

/**
 * The last `hours` refresh slots ending with the current one, each holding
 * the Action Center runs recorded in it.
 *
 * A slot runs from HH:15 to the next HH:15 — the scheduler's own grid —
 * because a run's row is stamped when it ENDS: bucketing by clock hour put a
 * 10:15 refresh that finished at 11:05 into the 11:00 slot, showing a gap at
 * 10:00 where a run happened and hiding the skipped 11:15. On the refresh
 * grid a run lands in the slot it started in whenever it takes under an
 * hour. Runs are plotted by slot, not by position, so a slot with no run is
 * a visible gap rather than two neighbours drawn side by side.
 */
export function hourlySlots(runs: ActionMetricsRun[], hours: number, now: number): HourSlot[] {
  const HOUR = 3_600_000;
  const offset = REFRESH_MINUTE * 60_000;
  const current = Math.floor((now - offset) / HOUR) * HOUR + offset;
  const first = current - (hours - 1) * HOUR;
  const slots: HourSlot[] = Array.from({ length: hours }, (_, i) => ({
    start: first + i * HOUR,
    runs: [],
  }));
  for (const run of actionRunsOldestFirst(runs)) {
    if (!run.recordedAt) continue;
    const i = Math.floor((parseUTC(run.recordedAt).getTime() - first) / HOUR);
    if (i >= 0 && i < hours) slots[i].runs.push(run);
  }
  return slots;
}

/**
 * Sum `pick` over a slot's runs; null for an empty slot (a gap, not a zero).
 * A slot with two runs (a manual refresh) counts both — dropping one would
 * make the chart and the totals disagree.
 */
export function slotSum(slot: HourSlot, pick: (r: ActionMetricsRun) => number): number | null {
  return slot.runs.length ? slot.runs.reduce((sum, r) => sum + pick(r), 0) : null;
}

/**
 * A short run-history list that still shows every pipeline: the newest
 * `perType` runs of each type, newest first overall. A plain "newest N" slice
 * of the merged feed is the shared cap /pipeline/history exists to avoid —
 * daily Senate/House runs push weekly Stock Trades out of view.
 */
export function historyPreview<T extends { pipelineType?: string; startedAt: string | null }>(
  runs: T[],
  perType: number
): T[] {
  const seen = new Map<string, number>();
  return runs.filter((r) => {
    const type = r.pipelineType ?? "senate";
    const n = seen.get(type) ?? 0;
    seen.set(type, n + 1);
    return n < perType;
  });
}

export interface WatchedPipeline {
  key: string;
  label: string;
  running: boolean;
  status: string | null | undefined;
  elapsedSeconds: number | null | undefined;
}

export interface FinishedRun {
  key: string;
  label: string;
  status: "completed" | "partial" | "failed";
  elapsedSeconds: number | null | undefined;
}

/**
 * Every pipeline that was running at the previous poll and is not now — all
 * of them, since two can finish between polls (a manual House run ending
 * alongside the nightly chain). A run that ends "partial" is reported as
 * partial, never folded into success.
 */
export function finishedSince(
  wasRunning: Record<string, boolean>,
  now: WatchedPipeline[]
): FinishedRun[] {
  return now
    .filter((p) => !p.running && wasRunning[p.key])
    .map((p) => ({
      key: p.key,
      label: p.label,
      status: p.status === "failed" ? "failed" : p.status === "partial" ? "partial" : "completed",
      elapsedSeconds: p.elapsedSeconds,
    }));
}

/**
 * Everything that makes scheduler.py's _hourly_action_refresh return without
 * running, with the age past which it stops waiting (its _is_stale limits).
 * Mirror that guard exactly: a pipeline missing here turns every nightly skip
 * into a false "missed", and a cap missing here lets a hung run hide a real
 * outage. Election does not block refreshes.
 */
const REFRESH_BLOCKERS: Record<string, { staleAfterMs: number; inMemoryFlag: boolean }> = {
  // Senate is checked through its DB row; the rest through in-process
  // running flags, which a restart clears even if the row still says
  // "running".
  senate: { staleAfterMs: 8 * 3_600_000, inMemoryFlag: false },
  house: { staleAfterMs: 8 * 3_600_000, inMemoryFlag: true },
  supplementary: { staleAfterMs: 8 * 3_600_000, inMemoryFlag: true },
  stock_trades: { staleAfterMs: 2 * 3_600_000, inMemoryFlag: true },
};
/** A previous refresh still running also blocks the next tick, for up to 4h. */
const REFRESH_STALE_AFTER_MS = 4 * 3_600_000;

export type SlotState = "ran" | "skipped" | "pending" | "missing";

export interface SlotContext {
  /** Which pipelines the backend says are running right now (in-memory flags). */
  runningNow: Record<string, boolean>;
  /** When the backend process started (ms) — a restart clears in-memory flags. */
  processStartedAt: number | null;
  /** The Action Center refresh in flight, if any (ms it started). */
  refreshStartedAt: number | null;
}

/**
 * Why each refresh slot does or doesn't have a run:
 * - ran: a run was recorded in it;
 * - skipped: empty, and its HH:15 tick fell while the scheduler was
 *   deliberately waiting — on a nightly pipeline run, or on a previous
 *   refresh still going;
 * - pending: empty, but its refresh is still running (or it is the current
 *   slot, whose refresh may be running now);
 * - missing: anything else — a refresh that crashed or never started.
 *
 * Each blocking interval ends where the scheduler stops honouring it: at the
 * run's end, at its stale limit, and — for pipelines tracked by an in-memory
 * flag — at the restart that cleared the flag, even if its DB row was left
 * "running".
 */
export function slotStates(
  slots: HourSlot[],
  pipelineRuns: PipelineTrendRun[],
  now: number,
  ctx: SlotContext
): SlotState[] {
  const busy: [number, number][] = [];
  for (const r of pipelineRuns) {
    const blocker = REFRESH_BLOCKERS[r.pipelineType];
    if (!blocker || !r.startedAt) continue;
    const start = parseUTC(r.startedAt).getTime();
    let end: number;
    if (r.status === "running") {
      // Checked before elapsedSeconds on purpose: the progress tracker
      // writes elapsed_seconds on every flush, so a running row carries the
      // time of its last flush, not its end.
      end = now;
      if (blocker.inMemoryFlag && !ctx.runningNow[r.pipelineType]) {
        // The row says running but the process doesn't: it was orphaned by
        // a restart, and the scheduler stopped waiting when that happened.
        end =
          ctx.processStartedAt != null && ctx.processStartedAt > start
            ? ctx.processStartedAt
            : start;
      }
    } else {
      end = start + (r.elapsedSeconds ?? 0) * 1000;
    }
    busy.push([start, Math.min(end, start + blocker.staleAfterMs)]);
  }
  const refreshSlot =
    ctx.refreshStartedAt == null
      ? -1
      : slots.findIndex(
          (s) => ctx.refreshStartedAt! >= s.start && ctx.refreshStartedAt! < s.start + 3_600_000
        );
  if (ctx.refreshStartedAt != null) {
    busy.push([ctx.refreshStartedAt, Math.min(now, ctx.refreshStartedAt + REFRESH_STALE_AFTER_MS)]);
  }

  return slots.map((slot, i) => {
    if (slot.runs.length > 0) return "ran";
    if (i === refreshSlot || i === slots.length - 1) return "pending";
    if (busy.some(([s, e]) => slot.start >= s && slot.start <= e)) return "skipped";
    return "missing";
  });
}
