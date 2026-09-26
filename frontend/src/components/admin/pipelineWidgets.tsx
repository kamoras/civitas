"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import type { PipelineHistoryRun, PipelineStepInfo } from "@/lib/api";
import { cacheHitRate, describeRun } from "@/lib/pipelineRuns";
import { formatDuration, formatTime, parseUTC } from "./format";
import { historyPreview } from "./trends";

export const PHASE_LABELS: Record<string, string> = {
  fetch: "FETCHING DATA",
  transform: "TRANSFORMING",
  analyze: "ANALYZING",
  explore: "EXPLORE DOCS",
  justices: "SCOTUS",
  presidents: "PRESIDENTS",
  finalize: "FINALIZING",
};

const PHASE_ORDER = ["fetch", "transform", "analyze", "finalize"] as const;

function ElapsedTimer({ startedAt }: { startedAt: string | null | undefined }) {
  const [elapsed, setElapsed] = useState("");
  useEffect(() => {
    if (!startedAt) return;
    const start = parseUTC(startedAt).getTime();
    const tick = () => {
      const s = Math.floor((Date.now() - start) / 1000);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      setElapsed(
        h > 0
          ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
          : `${m}:${String(sec).padStart(2, "0")}`
      );
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return <span className="tabular-nums">{elapsed || "0:00"}</span>;
}

function PhaseSteps({ currentPhase }: { currentPhase: string | null | undefined }) {
  const activeIdx = PHASE_ORDER.indexOf((currentPhase ?? "fetch") as (typeof PHASE_ORDER)[number]);
  return (
    <div className="flex items-center gap-1 text-xs font-mono tracking-wider">
      {PHASE_ORDER.map((p, i) => {
        const done = i < activeIdx;
        const active = i === activeIdx;
        return (
          <div key={p} className="flex items-center gap-1">
            {i > 0 && <span className={`w-4 h-px ${done ? "bg-phos" : "bg-phos"}`} />}
            <span
              className={
                active ? "text-signal-cyan animate-pulse" : done ? "text-ink-hi" : "text-ink-min"
              }
            >
              {done ? "✓ " : active ? "▶ " : ""}
              {PHASE_LABELS[p]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function formatEtaSeconds(etaSeconds: number): string {
  if (etaSeconds < 60) return `~${etaSeconds}s`;
  const m = Math.floor(etaSeconds / 60);
  const s = etaSeconds % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `~${h}h ${m % 60}m`;
  }
  return `~${m}m ${s}s`;
}

const NO_ESTIMATE = { eta: null, rate: null } as const;

function useAnalyzeEta(
  isAnalyze: boolean,
  processed: number,
  total: number,
  elapsedSeconds: number | null | undefined,
  unitLabel: string = "senator"
) {
  const liveAnchorRef = useRef<{ time: number; count: number } | null>(null);
  const [estimate, setEstimate] = useState<{ eta: string | null; rate: string | null }>({
    eta: null,
    rate: null,
  });

  // Whether an estimate means anything right now is a function of the props,
  // not something to remember. Deriving it means a finished or switched-away
  // pipeline cannot show the previous phase's ETA for even one frame, which
  // clearing the stored values from the effect allowed.
  const active = isAnalyze && total > 0 && total - processed > 0;

  useEffect(() => {
    if (!isAnalyze || total <= 0) {
      liveAnchorRef.current = null;
      return;
    }

    if (!liveAnchorRef.current) {
      liveAnchorRef.current = { time: Date.now(), count: processed };
    }

    const tick = () => {
      const anchor = liveAnchorRef.current;
      const remaining = total - processed;

      if (remaining <= 0) {
        setEstimate({ eta: null, rate: null });
        return;
      }

      const liveDelta = anchor ? processed - anchor.count : 0;
      const liveElapsed = anchor ? (Date.now() - anchor.time) / 1000 : 0;

      if (liveDelta > 0 && liveElapsed > 3) {
        const secPer = liveElapsed / liveDelta;
        setEstimate({
          rate: `${secPer.toFixed(0)}s/${unitLabel}`,
          eta: formatEtaSeconds(Math.round(remaining * secPer)),
        });
      } else if (processed > 0 && elapsedSeconds && elapsedSeconds > 0) {
        const secPer = elapsedSeconds / processed;
        setEstimate({
          rate: `~${secPer.toFixed(0)}s/${unitLabel}`,
          eta: formatEtaSeconds(Math.round(remaining * secPer)),
        });
      } else {
        setEstimate({ eta: null, rate: null });
      }
    };

    tick();
    const id = setInterval(tick, 2000);
    return () => clearInterval(id);
  }, [isAnalyze, processed, total, elapsedSeconds, unitLabel]);

  return active ? estimate : NO_ESTIMATE;
}

function StepProgressMini({ step }: { step: PipelineStepInfo }) {
  if (step.total == null || step.total === 0 || step.status === "pending") return null;
  const done = step.done ?? 0;
  const pct = Math.round((done / step.total) * 100);
  return (
    <div className="mt-1">
      <div
        className="w-full h-1 bg-white/[0.03] overflow-hidden"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${step.label} progress`}
      >
        <div
          className={`h-full transition-all duration-500 ${
            step.status === "active" ? "bg-signal-cyan" : "bg-phos"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono text-ink-min tabular-nums">
        {done}/{step.total}
      </span>
    </div>
  );
}

// Generic live-progress banner — was hand-written for Senate only
// (senatorsTotal/senatorsProcessed baked into the props type), so House,
// Stock Trades, and Supplementary pipelines never got this prominent
// "PIPELINE ACTIVE" view at all while actually running — only Senate's
// isRunning flag was ever checked at the call site. Senate and House
// share the fetch/transform/analyze/finalize phase vocabulary (PHASE_ORDER)
// so the breadcrumb generalizes directly; Stock Trades and Supplementary
// use different phase names (fetch-only; explore/justices/presidents), so
// showPhaseBreadcrumb is false for those — the step-by-step breakdown
// below still works for any phase vocabulary since it's driven by
// PHASE_LABELS, which already covers all of them. etaConfig is optional:
// only Senate and House have a clean "N of total processed" concept: to
// build one for Stock Trades / Supplementary; both are also normally far
// faster runs (~1min / ~40min vs Senate/House's ~1-2.5hr), where an ETA
// is much less valuable anyway.
export function PipelineProgressBar({
  title,
  isRunning,
  run,
  showPhaseBreadcrumb = true,
  etaConfig,
  statsRow,
}: {
  title: string;
  isRunning: boolean;
  run:
    | {
        startedAt: string | null;
        currentPhase?: string | null;
        elapsedSeconds: number | null;
        progressSteps?: PipelineStepInfo[] | null;
      }
    | null
    | undefined;
  showPhaseBreadcrumb?: boolean;
  etaConfig?: { processed: number; total: number; unitLabel: string };
  statsRow?: ReactNode;
}) {
  const phase = run?.currentPhase ?? "fetch";
  const total = etaConfig?.total ?? 0;
  const processed = etaConfig?.processed ?? 0;
  const elapsed = run?.elapsedSeconds ?? null;
  const isAnalyze = isRunning && phase === "analyze" && total > 0 && !!etaConfig;
  const { eta, rate } = useAnalyzeEta(
    isAnalyze,
    processed,
    total,
    elapsed,
    etaConfig?.unitLabel ?? "item"
  );

  if (!isRunning || !run) return null;

  const steps = run.progressSteps ?? [];
  const totalSteps = steps.length;
  const doneSteps = steps.filter((s) => s.status === "done" || s.status === "skipped").length;
  const overallPct = totalSteps > 0 ? Math.round((doneSteps / totalSteps) * 100) : 0;
  const activeStep = steps.find((s) => s.status === "active");

  const phaseGroups: { phase: string; label: string; steps: PipelineStepInfo[] }[] = [];
  for (const s of steps) {
    const last = phaseGroups[phaseGroups.length - 1];
    if (last && last.phase === s.phase) {
      last.steps.push(s);
    } else {
      phaseGroups.push({
        phase: s.phase,
        label: PHASE_LABELS[s.phase] ?? s.phase.toUpperCase(),
        steps: [s],
      });
    }
  }

  return (
    <div className="border border-signal-cyan/40 p-4 bg-signal-cyan/10">
      <div className="flex items-center justify-between mb-3">
        <span className="text-signal-cyan text-sm font-mono font-bold flex items-center gap-2">
          <span className="inline-block w-2 h-2 bg-signal-cyan animate-pulse" />
          {title}
        </span>
        <span className="text-ink text-xs font-mono">
          <ElapsedTimer startedAt={run.startedAt} />
        </span>
      </div>

      {showPhaseBreadcrumb && <PhaseSteps currentPhase={phase} />}

      {/* Overall progress bar */}
      <div className="mt-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-ink-lo text-xs font-mono">
            {activeStep ? activeStep.label.toUpperCase() : "INITIALIZING"}
          </span>
          <span className="text-ink-hi text-xs font-mono tabular-nums">
            {doneSteps}/{totalSteps} steps ({overallPct}%)
          </span>
        </div>
        <div
          className="w-full h-2 bg-white/[0.03] border border-white/[0.07] overflow-hidden"
          role="progressbar"
          aria-valuenow={overallPct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Pipeline overall progress"
        >
          <div
            className="h-full bg-signal-cyan transition-all duration-700"
            style={{ width: `${overallPct}%` }}
          />
        </div>
        {isAnalyze && eta && (
          <div className="flex items-center justify-between mt-1">
            <span className="text-ink-min text-xs font-mono tabular-nums">{rate}</span>
            <span className="text-signal-amber text-xs font-mono tabular-nums">ETA: {eta}</span>
          </div>
        )}
      </div>

      {/* Granular sub-steps grouped by phase */}
      {steps.length > 0 && (
        <div className="mt-4 space-y-3">
          {phaseGroups.map((group) => {
            const groupDone = group.steps.every(
              (s) => s.status === "done" || s.status === "skipped"
            );
            const groupActive = group.steps.some((s) => s.status === "active");
            return (
              <div key={group.phase}>
                <div
                  className={`text-xs font-mono tracking-wider mb-1.5 ${
                    groupActive ? "text-signal-cyan" : groupDone ? "text-ink" : "text-ink-min"
                  }`}
                >
                  {groupDone ? "✓ " : groupActive ? "▶ " : ""}
                  {group.label}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 pl-3 border-l border-white/[0.07]">
                  {group.steps.map((step) => (
                    <div key={step.key} className="flex items-start gap-2 min-h-[20px]">
                      <span
                        className={`mt-0.5 flex-shrink-0 w-3 text-center text-xs ${
                          step.status === "done"
                            ? "text-ink-hi"
                            : step.status === "active"
                              ? "text-signal-cyan animate-pulse"
                              : step.status === "skipped"
                                ? "text-ink-min"
                                : "text-ink-min"
                        }`}
                      >
                        {step.status === "done"
                          ? "✓"
                          : step.status === "active"
                            ? "●"
                            : step.status === "skipped"
                              ? "—"
                              : "○"}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span
                            className={`text-xs font-mono truncate ${
                              step.status === "active"
                                ? "text-signal-cyan"
                                : step.status === "done"
                                  ? "text-ink"
                                  : step.status === "skipped"
                                    ? "text-ink-min line-through"
                                    : "text-ink-min"
                            }`}
                          >
                            {step.label}
                          </span>
                          {step.detail && (step.status === "done" || step.status === "active") && (
                            <span className="text-xs font-mono text-ink-min truncate">
                              {step.detail}
                            </span>
                          )}
                        </div>
                        <StepProgressMini step={step} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {statsRow && (
        <div className="flex gap-4 mt-3 pt-2 border-t border-white/[0.07] text-xs text-ink-lo font-mono">
          {statsRow}
        </div>
      )}

      <style>{`
        @keyframes pipeline-scan {
          0%   { left: -33%; }
          100% { left: 100%; }
        }
      `}</style>
    </div>
  );
}

// --- Run History Table ---
const HISTORY_PREVIEW_PER_TYPE = 5;

export function RunHistory({ runs: allRuns }: { runs: PipelineHistoryRun[] }) {
  // The feed carries up to 20 runs of each of five pipelines — 100 rows,
  // which buried everything below it. The preview keeps the newest few of
  // EACH pipeline rather than the newest N overall: a shared cap is exactly
  // what /pipeline/history avoids, since daily Senate/House runs would push
  // a weekly Stock Trades failure out of view.
  const [showAll, setShowAll] = useState(false);
  if (allRuns.length === 0)
    return <p className="text-ink-min text-xs">No pipeline runs recorded.</p>;
  const preview = historyPreview(allRuns, HISTORY_PREVIEW_PER_TYPE);
  const runs = showAll ? allRuns : preview;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-ink-lo border-b border-white/[0.07]">
            <th scope="col" className="text-left py-1 pr-3">
              TYPE
            </th>
            <th scope="col" className="text-left py-1 pr-3">
              STARTED
            </th>
            <th scope="col" className="text-left py-1 pr-3">
              STATUS
            </th>
            <th scope="col" className="text-left py-1 pr-3">
              DURATION
            </th>
            <th scope="col" className="text-right py-1 pr-3">
              PROCESSED
            </th>
            <th scope="col" className="text-right py-1 pr-3">
              LLM
            </th>
            <th scope="col" className="text-right py-1">
              CACHE
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            // Every per-type decision comes from one exhaustive table (see
            // lib/pipelineRuns.ts) rather than a chain of negations — that
            // chain is what silently rendered Election runs as SENATE.
            const display = describeRun(r);
            const hitRate = display.hasLlmStats ? cacheHitRate(r) : null;
            const statusColor =
              r.status === "completed"
                ? "text-ink-hi"
                : r.status === "partial"
                  ? "text-signal-amber"
                  : r.status === "failed"
                    ? "text-signal-magenta"
                    : r.status === "running"
                      ? "text-signal-cyan animate-pulse"
                      : "text-ink-lo";
            return (
              <tr
                key={`${r.pipelineType ?? "senate"}-${r.id}`}
                className="border-b border-white/[0.07] hover:bg-white/[0.03]"
              >
                <td className="py-1.5 pr-3">
                  <span className={display.hasLlmStats ? "text-ink-lo" : "text-signal-cyan"}>
                    {display.label}
                  </span>
                </td>
                <td className="py-1.5 pr-3 text-ink">{formatTime(r.startedAt)}</td>
                <td className="py-1.5 pr-3">
                  <span className={statusColor}>{r.status.toUpperCase()}</span>
                  {r.errorMessage && (
                    <span className="ml-2 text-ink-lo text-xs" title={r.errorMessage}>
                      ⚠
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-ink-lo">{formatDuration(r.elapsedSeconds)}</td>
                <td className="py-1.5 pr-3 text-right text-ink-lo">
                  {display.processed}
                  {display.failed > 0 && (
                    <span className="text-signal-magenta ml-1">({display.failed}F)</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right text-ink-lo">
                  {display.hasLlmStats ? (r.llmCalls ?? 0) : "—"}
                </td>
                <td className="py-1.5 text-right text-ink-lo">
                  {hitRate === null ? "—" : `${hitRate}%`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {allRuns.length > preview.length && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          aria-expanded={showAll}
          className="mt-2 text-xs font-mono text-ink-min hover:text-ink-lo"
        >
          {showAll
            ? `Show the newest ${HISTORY_PREVIEW_PER_TYPE} of each pipeline only`
            : `Show all ${allRuns.length} runs`}
        </button>
      )}
    </div>
  );
}

/**
 * One "PIPELINE  STATUS · detail" row plus its stuck-run clear button.
 * Shared by the House and Stock Trades rows (which both track a
 * possibly-stuck DB run separate from the in-memory running flag); Senate
 * has no stuck-run concept and renders its own row inline.
 */
export function PipelineStatusRow({
  label,
  isRunning,
  run,
  isStuck,
  statusClassName,
  detail,
  onClear,
  clearing,
}: {
  label: string;
  isRunning: boolean;
  run: { status: string } | null | undefined;
  isStuck: boolean;
  statusClassName: string;
  detail?: ReactNode;
  onClear?: () => Promise<void>;
  clearing?: boolean;
}) {
  return (
    <>
      <div className="flex justify-between items-center">
        <span className="text-ink-lo">{label}</span>
        <span className={isRunning ? "text-signal-cyan animate-pulse" : "text-ink-lo"}>
          {isRunning ? (
            "RUNNING"
          ) : run ? (
            <span>
              <span className={statusClassName}>
                {isStuck ? "STUCK" : run.status.toUpperCase()}
              </span>
              {detail}
            </span>
          ) : (
            "IDLE"
          )}
        </span>
      </div>
      {isStuck && onClear && (
        <div className="flex justify-end">
          <button
            disabled={clearing}
            onClick={onClear}
            className="text-xs font-mono text-signal-amber hover:text-signal-amber border border-signal-amber/30 hover:border-signal-amber/60
                       px-2 py-0.5  transition-colors disabled:opacity-40"
          >
            {clearing ? "CLEARING..." : "[CLEAR STUCK RUN]"}
          </button>
        </div>
      )}
    </>
  );
}

// Generic per-pipeline "last run" detail card: status/started/duration/error
// plus the full step breakdown, for any pipeline type that shares that base
// shape (HouseRunInfo, StockTradesRunInfo, SupplementaryRunInfo all do — see
// api.ts). Senate's own equivalent card is hand-written below with its
// richer, senate-specific stats (senators/bills/LLM calls/cache hit rate)
// left as-is rather than folded into this generic shape, to avoid touching
// working code — but before this, House/Stock Trades/Supplementary had no
// equivalent detail card at all, only a compact one-line status row in the
// System section below with steps collapsed inside it. This gives them the
// same prominent, dedicated view Senate already had.
export function PipelineRunDetailCard({
  title,
  run,
  extraStats,
}: {
  title: string;
  run:
    | {
        status: string;
        startedAt: string | null;
        completedAt: string | null;
        elapsedSeconds: number | null;
        errorMessage: string | null;
        progressSteps?: PipelineStepInfo[] | null;
      }
    | null
    | undefined;
  extraStats?: ReactNode;
}) {
  if (!run) return null;
  return (
    <div className="panel">
      <TerminalTitlebar title={title} />
      <div className="p-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-mono">
          <div>
            <span className="text-ink-lo text-xs block">STATUS</span>
            <span
              className={
                run.status === "completed"
                  ? "text-ink-hi"
                  : run.status === "failed"
                    ? "text-signal-magenta"
                    : "text-signal-cyan"
              }
            >
              {run.status.toUpperCase()}
            </span>
          </div>
          <div>
            <span className="text-ink-lo text-xs block">STARTED</span>
            <span>{formatTime(run.startedAt)}</span>
          </div>
          <div>
            <span className="text-ink-lo text-xs block">DURATION</span>
            <span>{formatDuration(run.elapsedSeconds)}</span>
          </div>
          {extraStats}
        </div>
        {run.errorMessage && (
          <div className="mt-3 p-2 border border-signal-magenta/40 bg-signal-magenta/10">
            <span className="text-signal-magenta text-xs font-mono">ERROR: {run.errorMessage}</span>
          </div>
        )}
        <LastRunSteps steps={run.progressSteps} />
      </div>
    </div>
  );
}

export function LastRunSteps({ steps }: { steps?: PipelineStepInfo[] | null }) {
  const [expanded, setExpanded] = useState(false);
  if (!steps || steps.length === 0) return null;

  return (
    <div className="mt-4 border-t border-white/[0.07] pt-3">
      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`Step breakdown, ${steps.length} steps`}
        className="text-xs font-mono text-ink-lo hover:text-phos transition-colors"
      >
        {expanded ? "▼" : "▶"} STEP BREAKDOWN ({steps.length} steps)
      </button>
      {expanded && (
        <div className="mt-2 space-y-0.5">
          {steps.map((step) => (
            <div key={step.key} className="flex items-center gap-2 text-xs font-mono py-0.5">
              <span
                className={`w-3 text-center flex-shrink-0 ${
                  step.status === "done"
                    ? "text-ink-hi"
                    : step.status === "skipped"
                      ? "text-ink-min"
                      : "text-ink-min"
                }`}
              >
                {step.status === "done" ? "✓" : step.status === "skipped" ? "—" : "○"}
              </span>
              <span
                className={`w-40 truncate ${
                  step.status === "skipped" ? "text-ink-min line-through" : "text-ink"
                }`}
              >
                {step.label}
              </span>
              {step.detail && <span className="text-ink-min truncate">{step.detail}</span>}
              {step.total != null && step.total > 0 && (
                <span className="text-ink-min tabular-nums ml-auto">
                  {step.done ?? step.total}/{step.total}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
