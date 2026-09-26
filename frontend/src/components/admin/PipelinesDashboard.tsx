"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  clearStuckElectionPipeline,
  clearStuckHousePipeline,
  clearStuckStockTradesPipeline,
  clearStuckSupplementaryPipeline,
  fetchAdminPipelineTimings,
  fetchAdminPipelineTrend,
  type AdminDashboard,
  type AdminPipelineStatus,
  type PipelineHistoryRun,
  type PipelineTimings,
  type PipelineTrendRun,
} from "@/lib/api";
import LineChart from "./charts/LineChart";
import { PIPELINE_COLORS, PIPELINE_LABELS, SERIES, STATUS } from "./charts/palette";
import { formatSecondsShort, niceDurationTicks } from "./charts/scale";
import { formatDay, formatDuration, formatTime, statusClass } from "./format";
import {
  LastRunSteps,
  PHASE_LABELS,
  PipelineRunDetailCard,
  PipelineStatusRow,
  RunHistory,
} from "./pipelineWidgets";
import { RANGE_OPTIONS } from "./TrafficDashboard";
import { dayWindow, durationPoints, runsPerDay, utcToday } from "./trends";
import { Panel, Segmented, StatTile } from "./widgets";

const PIPELINE_ORDER = ["senate", "house", "supplementary", "stock_trades", "election"] as const;

// --- Status of every pipeline, with the stuck-run escape hatch ------------------

type ClearFn = (token: string) => Promise<unknown>;

function StuckAwareRow({
  label,
  isRunning,
  run,
  detail,
  token,
  clear,
  onCleared,
}: {
  label: string;
  isRunning: boolean;
  run: { status: string; progressSteps?: PipelineHistoryRun["progressSteps"] } | null | undefined;
  detail?: ReactNode;
  token: string;
  clear?: ClearFn;
  onCleared: () => Promise<void> | void;
}) {
  const [clearing, setClearing] = useState(false);
  const isStuck = run?.status === "running" && !isRunning;
  return (
    <>
      <PipelineStatusRow
        label={label}
        isRunning={isRunning}
        run={run}
        isStuck={isStuck}
        statusClassName={statusClass(run?.status, isStuck)}
        detail={detail}
        clearing={clearing}
        onClear={
          clear &&
          (async () => {
            setClearing(true);
            try {
              await clear(token);
              await onCleared();
            } catch {}
            setClearing(false);
          })
        }
      />
      <LastRunSteps steps={run?.progressSteps} />
    </>
  );
}

function PipelineStatusPanel({
  token,
  status,
  dashboard,
  onChanged,
}: {
  token: string;
  status: AdminPipelineStatus | null;
  dashboard: AdminDashboard | null;
  onChanged: () => Promise<void> | void;
}) {
  const senate = status?.lastRun;
  const house = status?.houseLastRun;
  const stock = status?.stockTradesLastRun;
  const supp = status?.supplementaryLastRun;
  const election = status?.electionLastRun;

  return (
    <Panel title="Pipeline status">
      <div className="space-y-1.5 text-sm font-mono">
        <StuckAwareRow
          label="SENATE"
          isRunning={!!status?.isRunning}
          run={senate}
          token={token}
          onCleared={onChanged}
          detail={
            senate && (
              <>
                <span className="text-ink-lo">
                  {" "}
                  · {senate.senatorsProcessed}/{senate.senatorsTotal}
                </span>
                {senate.senatorsFailed > 0 && (
                  <span className="text-signal-magenta"> · {senate.senatorsFailed}F</span>
                )}
              </>
            )
          }
        />
        <StuckAwareRow
          label="HOUSE"
          isRunning={!!status?.houseIsRunning}
          run={house}
          token={token}
          clear={clearStuckHousePipeline}
          onCleared={onChanged}
          detail={
            house && (
              <>
                {house.repsTotal > 0 && (
                  <span className="text-ink-lo">
                    {" "}
                    · {house.repsProcessed}/{house.repsTotal}
                  </span>
                )}
                {(house.repsFailed ?? 0) > 0 && (
                  <span className="text-signal-magenta"> · {house.repsFailed}F</span>
                )}
              </>
            )
          }
        />
        {/* Supplementary: explore docs + SCOTUS + presidents. Independent of
            Senate (see supplementary_pipeline.py). */}
        <StuckAwareRow
          label="SUPPLEMENTARY"
          isRunning={!!status?.supplementaryIsRunning}
          run={supp}
          token={token}
          clear={clearStuckSupplementaryPipeline}
          onCleared={onChanged}
          detail={
            supp && (
              <span className="text-ink-lo">
                {" "}
                · {supp.exploreDocsIngested} docs ·{" "}
                {supp.justicesSkipped ? "SCOTUS skipped" : `${supp.justicesScored} justices`} ·{" "}
                {supp.presidentsUpdated} pres
              </span>
            )
          }
        />
        <StuckAwareRow
          label="STOCK TRADES"
          isRunning={!!status?.stockTradesIsRunning}
          run={stock}
          token={token}
          clear={clearStuckStockTradesPipeline}
          onCleared={onChanged}
          detail={
            stock && (
              <span className="text-ink-lo">
                {" "}
                · {stock.houseTradesIngested}H/{stock.senateTradesIngested}S/
                {stock.presidentTradesIngested}P
              </span>
            )
          }
        />
        {/* Election had a clear-stuck endpoint on the backend and no row
            here at all, so a stuck election run could only be cleared with
            curl. */}
        <StuckAwareRow
          label="ELECTION"
          isRunning={!!status?.electionIsRunning}
          run={election}
          token={token}
          clear={clearStuckElectionPipeline}
          onCleared={onChanged}
          detail={
            election && (
              <span className="text-ink-lo">
                {" "}
                · {election.candidatesSynced ?? 0} candidates · {election.financialsRefreshed ?? 0}{" "}
                financials
              </span>
            )
          }
        />
        <div className="border-t border-white/[0.07] pt-2 mt-2 space-y-1.5">
          <div className="flex justify-between">
            <span className="text-ink-lo">SCHEDULE</span>
            <span className="text-ink">{dashboard?.pipeline.cronSchedule ?? "—"}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-lo">NEXT RUN</span>
            <span className="text-ink">{formatTime(dashboard?.pipeline.nextScheduled)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-ink-lo">SENATE RUNS, ALL TIME</span>
            <span className="text-ink">
              {dashboard?.pipeline.totalRuns ?? 0}{" "}
              <span className="text-ink-min">
                ({dashboard?.pipeline.successfulRuns ?? 0} ok ·{" "}
                {dashboard?.pipeline.failedRuns ?? 0} failed)
              </span>
            </span>
          </div>
        </div>
      </div>
    </Panel>
  );
}

// --- Per-phase timings --------------------------------------------------------

const TIMING_KINDS: { value: string; label: string }[] = [
  { value: "pipeline_runs", label: "SENATE" },
  { value: "house_pipeline_runs", label: "HOUSE" },
  { value: "supplementary_pipeline_runs", label: "SUPP" },
  { value: "stock_trades_pipeline_runs", label: "STOCK" },
  { value: "election_pipeline_runs", label: "ELECTION" },
];

// Canonical order a pipeline's phases run in. Colours are assigned by
// position in this list (among the phases the selected pipeline has), so a
// phase keeps its colour on every run and across the chart and the bars —
// assigning by each run's own sort order would repaint "fetch" whenever
// another phase happened to take longer.
const PHASE_ORDER = [
  "fetch",
  "transform",
  "analyze",
  "explore",
  "justices",
  "presidents",
  "finalize",
];

function phaseColors(phases: string[]): Record<string, string> {
  const ordered = [...new Set(phases)].sort((a, b) => {
    const ia = PHASE_ORDER.indexOf(a);
    const ib = PHASE_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return Object.fromEntries(ordered.map((p, i) => [p, SERIES[i % SERIES.length]]));
}

const phaseLabel = (p: string) => PHASE_LABELS[p] ?? (p ? p.toUpperCase() : "UNTAGGED");

/**
 * Per-phase duration for recent runs of one pipeline.
 *
 * The run history already shows total elapsed time, which says a run got
 * slower but not where. This splits each run by the fetch/transform/analyze/
 * finalize tag on its steps — the split that separates waiting on
 * rate-limited external APIs from local compute.
 */
function PhaseTimings({ token }: { token: string }) {
  const [kind, setKind] = useState("pipeline_runs");
  const [timings, setTimings] = useState<PipelineTimings | null>(null);
  // Which kind the current `timings` belongs to. Loading is derived from
  // this rather than set synchronously inside the effect — the latter
  // trips react-hooks/set-state-in-effect and causes a cascading render.
  const [loadedKind, setLoadedKind] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const loading = loadedKind !== kind;

  useEffect(() => {
    let cancelled = false;
    fetchAdminPipelineTimings(token, kind, 20)
      .then((t) => {
        if (!cancelled) setTimings(t);
      })
      .catch(() => {
        if (!cancelled) setTimings(null);
      })
      .finally(() => {
        if (!cancelled) setLoadedKind(kind);
      });
    return () => {
      cancelled = true;
    };
  }, [token, kind]);

  const runs = timings?.runs ?? [];
  const oldestFirst = [...runs].reverse();
  const maxTotal = Math.max(1, ...runs.map((r) => r.totalSeconds));
  const colors = phaseColors(runs.flatMap((r) => r.phases.map((p) => p.phase)));
  const phases = Object.keys(colors);
  const runLabel = (r: (typeof runs)[number]) =>
    `#${r.runId}${r.startedAt ? ` · ${formatTime(r.startedAt)}` : ""}`;

  return (
    <Panel title="Phase timings">
      <div className="mb-4">
        <Segmented label="PIPELINE" options={TIMING_KINDS} value={kind} onChange={setKind} />
      </div>

      {!loading && runs.length === 0 ? (
        <div className="text-ink-min text-xs font-mono">
          No phase timings recorded yet — they are written as each run completes its steps.
        </div>
      ) : (
        <div className={loading ? "opacity-50 transition-opacity" : "transition-opacity"}>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <LineChart
              title="TIME PER PHASE, RUN OVER RUN"
              subtitle="which phase absorbed a slowdown"
              xLabels={oldestFirst.map(runLabel)}
              xTickLabel={(i) => `#${oldestFirst[i].runId}`}
              series={phases.map((p) => ({
                key: p,
                label: phaseLabel(p),
                color: colors[p],
                values: oldestFirst.map(
                  (r) => r.phases.find((x) => x.phase === p)?.seconds ?? null
                ),
              }))}
              formatValue={formatDuration}
              formatTick={formatSecondsShort}
              yTicks={niceDurationTicks}
              emptyMessage={loading ? "Loading…" : "No timed runs."}
            />
            {!loading && runs.every((r) => r.blockedPct === 0) ? (
              <div>
                <div className="text-xs font-mono tracking-wider text-ink-lo">
                  SHARE OF RUN BLOCKED ON RATE LIMITS
                </div>
                <p className="mt-2 text-xs font-mono text-ink-min">
                  None — no run in this window waited on an external API&apos;s rate limit, so its
                  time is all local work.
                </p>
              </div>
            ) : (
              <LineChart
                title="SHARE OF RUN BLOCKED ON RATE LIMITS"
                subtitle="high = throughput-bound on someone else's API; faster hardware won't help"
                xLabels={oldestFirst.map(runLabel)}
                xTickLabel={(i) => `#${oldestFirst[i].runId}`}
                series={[
                  {
                    key: "blocked",
                    label: "Blocked",
                    color: SERIES[3],
                    values: oldestFirst.map((r) => r.blockedPct),
                  },
                ]}
                formatValue={(v) => `${v.toFixed(1)}%`}
                emptyMessage={loading ? "Loading…" : "No timed runs."}
              />
            )}
          </div>

          <div className="mt-6 mb-2 flex flex-wrap gap-x-4 gap-y-1" aria-hidden="true">
            {phases.map((p) => (
              <span key={p} className="flex items-center gap-1.5 text-xs font-mono text-ink-lo">
                <span className="inline-block h-2 w-3" style={{ background: colors[p] }} />
                {phaseLabel(p)}
              </span>
            ))}
          </div>
          <div className="space-y-3">
            {runs.map((run) => (
              <div key={run.runId}>
                <button
                  type="button"
                  onClick={() => setExpanded(expanded === run.runId ? null : run.runId)}
                  aria-expanded={expanded === run.runId}
                  className="w-full text-left"
                >
                  <div className="flex items-baseline justify-between gap-3 mb-1">
                    <span className="text-ink-min text-xs font-mono tabular-nums shrink-0">
                      {runLabel(run)}
                    </span>
                    <span className="text-ink text-xs font-mono tabular-nums shrink-0">
                      {formatDuration(run.totalSeconds)}
                      {run.blockedPct > 0 && (
                        <span
                          className={
                            run.blockedPct >= 50 ? "text-signal-magenta ml-2" : "text-ink-min ml-2"
                          }
                        >
                          {run.blockedPct}% blocked
                        </span>
                      )}
                    </span>
                  </div>
                  {/* Stacked bar: each phase's share of this run, widths
                      relative to the slowest run in the window so runs are
                      comparable to each other, not just internally. The 2px
                      gap separates segments; phases keep one colour each. */}
                  <div
                    className="flex h-2 gap-[2px]"
                    role="img"
                    aria-label={run.phases
                      .map((p) => `${phaseLabel(p.phase)} ${formatDuration(p.seconds)} (${p.pct}%)`)
                      .join(", ")}
                    style={{ width: `${(run.totalSeconds / maxTotal) * 100}%` }}
                  >
                    {[...run.phases]
                      .sort((a, b) => phases.indexOf(a.phase) - phases.indexOf(b.phase))
                      .map((p) => (
                        <div
                          key={p.phase}
                          className="h-full"
                          style={{ width: `${p.pct}%`, background: colors[p.phase] }}
                          title={`${phaseLabel(p.phase)}: ${formatDuration(p.seconds)} (${p.pct}%)`}
                        />
                      ))}
                  </div>
                </button>

                {expanded === run.runId && (
                  <div className="mt-2 pl-2 border-l border-white/[0.07] space-y-1">
                    {run.phases.map((p) => (
                      <div key={p.phase} className="flex justify-between gap-3">
                        <span className="text-ink-lo text-xs font-mono">
                          {phaseLabel(p.phase)} ({p.steps})
                        </span>
                        <span className="text-ink-lo text-xs font-mono tabular-nums">
                          {formatDuration(p.seconds)} · {p.pct}%
                        </span>
                      </div>
                    ))}
                    {run.rateLimitSources.length > 0 && (
                      <div className="pt-1 mt-1 border-t border-white/[0.07] space-y-0.5">
                        <div className="text-ink-min text-xs font-mono tracking-wider">
                          BLOCKED ON RATE LIMITS
                        </div>
                        {run.rateLimitSources.map((s) => (
                          <div key={s.source} className="flex justify-between gap-3">
                            <span className="text-ink-min text-xs font-mono truncate">
                              {s.source} · {s.requests.toLocaleString()} req
                            </span>
                            <span className="text-ink-min text-xs font-mono tabular-nums shrink-0">
                              {formatDuration(s.blockedSeconds)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="pt-1 mt-1 border-t border-white/[0.07] space-y-0.5">
                      {run.steps.slice(0, 8).map((s) => (
                        <div key={s.stepKey} className="flex justify-between gap-3">
                          <span className="text-ink-min text-xs font-mono truncate">
                            {s.label || s.stepKey}
                            {s.status !== "done" ? ` [${s.status}]` : ""}
                          </span>
                          <span className="text-ink-min text-xs font-mono tabular-nums shrink-0">
                            {formatDuration(s.seconds)}
                            {s.blockedSeconds > 0 && (
                              <span className="text-ink-min">
                                {" "}
                                ({formatDuration(s.blockedSeconds)} blocked)
                              </span>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                    {run.untimedSteps > 0 && (
                      <div className="text-ink-min text-xs font-mono pt-1">
                        {run.untimedSteps} step(s) without a duration — excluded from totals.
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

// --- Last-run cards -----------------------------------------------------------

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <span className="text-ink-lo text-xs block">{label}</span>
      <span>{children}</span>
    </div>
  );
}

function LastRunCards({
  status,
  dashboard,
}: {
  status: AdminPipelineStatus | null;
  dashboard: AdminDashboard | null;
}) {
  const senate = dashboard?.pipeline.lastRun;
  const house = status?.houseLastRun;
  const stock = status?.stockTradesLastRun;
  const supp = status?.supplementaryLastRun;
  const election = status?.electionLastRun;
  const cacheTotal = senate ? senate.cacheHits + senate.cacheMisses : 0;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <PipelineRunDetailCard
        title="Senate — last run"
        run={senate}
        extraStats={
          senate && (
            <>
              <Stat label="SENATORS">
                {senate.senatorsProcessed}/{senate.senatorsTotal}
                {senate.senatorsFailed > 0 && (
                  <span className="text-signal-magenta ml-1">({senate.senatorsFailed} failed)</span>
                )}
              </Stat>
              <Stat label="LLM CALLS">{senate.llmCalls}</Stat>
              <Stat label="BILLS CLASSIFIED">{senate.billsClassified}</Stat>
              <Stat label="CACHE HIT RATE">
                {cacheTotal > 0 ? `${Math.round((senate.cacheHits / cacheTotal) * 100)}%` : "—"}
              </Stat>
            </>
          )
        }
      />
      <PipelineRunDetailCard
        title="House — last run"
        run={house}
        extraStats={
          house && (
            <Stat label="REPS">
              {house.repsProcessed}/{house.repsTotal}
              {house.repsFailed > 0 && (
                <span className="text-signal-magenta ml-1">({house.repsFailed} failed)</span>
              )}
            </Stat>
          )
        }
      />
      <PipelineRunDetailCard
        title="Supplementary — last run"
        run={supp}
        extraStats={
          supp && (
            <>
              <Stat label="EXPLORE DOCS">{supp.exploreDocsIngested}</Stat>
              <Stat label="SCOTUS">
                {supp.justicesSkipped ? "skipped" : `${supp.justicesScored} scored`}
              </Stat>
              <Stat label="PRESIDENTS">{supp.presidentsUpdated}</Stat>
            </>
          )
        }
      />
      <PipelineRunDetailCard
        title="Stock trades — last run"
        run={stock}
        extraStats={
          stock && (
            <Stat label="TRADES INGESTED">
              {stock.houseTradesIngested}H / {stock.senateTradesIngested}S /{" "}
              {stock.presidentTradesIngested}P
            </Stat>
          )
        }
      />
      <PipelineRunDetailCard
        title="Election — last run"
        run={election}
        extraStats={
          election && (
            <>
              <Stat label="CANDIDATES SYNCED">{election.candidatesSynced ?? 0}</Stat>
              <Stat label="FINANCIALS">{election.financialsRefreshed ?? 0}</Stat>
              <Stat label="COVERAGE">{election.coverageItemsIngested ?? 0}</Stat>
            </>
          )
        }
      />
    </div>
  );
}

// --- The sub-dashboard ----------------------------------------------------------

export function PipelinesDashboard({
  token,
  status,
  dashboard,
  history,
  onChanged,
}: {
  token: string;
  status: AdminPipelineStatus | null;
  dashboard: AdminDashboard | null;
  history: PipelineHistoryRun[];
  onChanged: () => Promise<void> | void;
}) {
  const [range, setRange] = useState(30);
  const [trend, setTrend] = useState<PipelineTrendRun[] | null>(null);
  const [loadedRange, setLoadedRange] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminPipelineTrend(token, range)
      .then((t) => {
        if (!cancelled) setTrend(t.runs);
      })
      .catch(() => {
        if (!cancelled) setTrend([]);
      })
      .finally(() => {
        if (!cancelled) setLoadedRange(range);
      });
    return () => {
      cancelled = true;
    };
  }, [token, range]);

  const stale = loadedRange !== range;
  const runs = trend ?? [];
  const dates = dayWindow(range, utcToday());
  const perDay = runsPerDay(runs, dates);
  const finished = runs.filter((r) => r.status !== "running");
  const failed = finished.filter((r) => r.status === "failed").length;
  const partial = finished.filter((r) => r.status === "partial").length;
  const dayLabels = dates.map(formatDay);

  return (
    <div className="space-y-6">
      <Segmented label="RANGE" options={RANGE_OPTIONS} value={range} onChange={setRange} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label={`Runs in ${range}d`}
          value={finished.length.toLocaleString()}
          note="all pipelines, finished"
        />
        <StatTile
          label="Failed"
          value={failed.toLocaleString()}
          tone={failed > 0 ? "text-signal-magenta" : "text-ink-hi"}
          note={finished.length ? `${Math.round((failed / finished.length) * 100)}% of runs` : "—"}
        />
        <StatTile
          label="Partial"
          value={partial.toLocaleString()}
          tone={partial > 0 ? "text-signal-amber" : "text-ink-hi"}
          note="finished with some members failed"
        />
        <StatTile
          label="Next scheduled"
          value={<span className="text-base">{formatTime(dashboard?.pipeline.nextScheduled)}</span>}
          note={dashboard?.pipeline.cronSchedule}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PipelineStatusPanel
          token={token}
          status={status}
          dashboard={dashboard}
          onChanged={onChanged}
        />
        <Panel title="Outcomes">
          <LineChart
            title="FINISHED RUNS PER DAY, BY OUTCOME"
            subtitle="all five pipelines together; today is still filling in"
            xLabels={dayLabels}
            series={[
              {
                key: "completed",
                label: "Completed",
                color: STATUS.good,
                values: perDay.completed,
              },
              { key: "partial", label: "Partial", color: STATUS.warning, values: perDay.partial },
              { key: "failed", label: "Failed", color: STATUS.critical, values: perDay.failed },
            ]}
            formatValue={(v) => String(v)}
            stale={stale}
            height={190}
            emptyMessage={trend ? "No runs in this range." : "Loading…"}
          />
        </Panel>
      </div>

      <Panel title="Run duration">
        <p className="mb-4 text-xs font-mono text-ink-min">
          One chart per pipeline — their run lengths differ by two orders of magnitude, so a shared
          axis would flatten the short ones. Each point is one finished run; failed runs are
          included, since a run that died early is itself worth seeing.
        </p>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {PIPELINE_ORDER.map((type) => {
            const points = durationPoints(runs, type);
            const labels = points.map(
              (p) => `${formatTime(p.startedAt)}${p.status !== "completed" ? ` (${p.status})` : ""}`
            );
            const last = points[points.length - 1];
            return (
              <LineChart
                key={type}
                title={PIPELINE_LABELS[type].toUpperCase()}
                xLabels={labels}
                xTickLabel={(i) => formatDay(points[i].startedAt.slice(0, 10))}
                series={[
                  {
                    key: type,
                    label: `${PIPELINE_LABELS[type]} duration`,
                    color: PIPELINE_COLORS[type],
                    values: points.map((p) => p.seconds),
                  },
                ]}
                formatValue={formatDuration}
                formatTick={formatSecondsShort}
                yTicks={niceDurationTicks}
                stale={stale}
                height={110}
                headline={
                  last ? (
                    <span className="text-xs">
                      last{" "}
                      <span className={statusClass(last.status)}>
                        {formatDuration(last.seconds)}
                      </span>
                    </span>
                  ) : undefined
                }
                emptyMessage={trend ? "No finished runs in this range." : "Loading…"}
              />
            );
          })}
        </div>
      </Panel>

      <PhaseTimings token={token} />

      <LastRunCards status={status} dashboard={dashboard} />

      <Panel title="Run history">
        <RunHistory runs={history} />
      </Panel>
    </div>
  );
}
