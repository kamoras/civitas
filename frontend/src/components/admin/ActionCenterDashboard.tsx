"use client";

import { useEffect, useState } from "react";
import { useNow } from "@/hooks/useNow";
import { fetchAdminActionMetrics, type ActionMetrics, type ActionRefreshState } from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { formatCompact } from "./charts/scale";
import { formatDuration, formatTime, parseUTC } from "./format";
import { actionRunsOldestFirst } from "./trends";
import { Panel, RankBars, Segmented, StatTile } from "./widgets";

// --- Action Center Status Panel ---
const ACTION_STAGE_LABELS: Record<string, string> = {
  fetch: "FETCHING ARTICLES",
  filter: "FILTERING RELEVANCE",
  cluster: "CLUSTERING TOPICS",
  rank: "RANKING CLUSTERS",
  issues: "GENERATING ISSUES",
  monitors: "UPDATING MONITORS",
  theme: "GENERATING THEME",
  stories: "WRITING STORIES",
  bluesky: "POSTING TO BLUESKY",
  cleanup: "CLEANUP",
};

// Seconds since a run started, ticking. Nothing is stored: the elapsed time is
// a function of the shared clock and the start stamp, so it cannot go stale,
// and stopping a run reports 0 immediately rather than one tick later.
function useElapsedSeconds(startIso: string | null, running: boolean): number {
  const now = useNow();
  if (!running || !startIso) return 0;
  return Math.max(0, Math.round((now - parseUTC(startIso).getTime()) / 1000));
}

function ActionCenterStatus({ ac }: { ac: ActionRefreshState | null }) {
  // Use a stable startedAt ref so the timer only resets when a genuinely new run begins
  const startedAt = ac?.isRunning ? ac.startedAt : null;
  const totalElapsed = useElapsedSeconds(startedAt, ac?.isRunning ?? false);

  if (!ac || (!ac.isRunning && !ac.lastCompletedAt)) {
    return (
      <div className="p-4 text-xs font-mono text-ink-min">
        No data yet — status available after first refresh.
      </div>
    );
  }

  const stageLabel = ac.stage ? (ACTION_STAGE_LABELS[ac.stage] ?? ac.stage.toUpperCase()) : null;

  // Parse N/M progress detail
  const progressMatch = ac.stageDetail ? /^(\d+)\/(\d+)/.exec(ac.stageDetail) : null;
  const progressDone = progressMatch ? parseInt(progressMatch[1]) : null;
  const progressTotal = progressMatch ? parseInt(progressMatch[2]) : null;
  const progressPct =
    progressDone !== null && progressTotal && progressTotal > 0
      ? Math.round((progressDone / progressTotal) * 100)
      : null;

  // Sub-step detail (text after N/M)
  const subStep = ac.stageDetail && !progressMatch ? ac.stageDetail : null;

  return (
    <div className="p-4 space-y-3 text-xs font-mono">
      {/* Status + elapsed */}
      <div className="flex items-center justify-between">
        <span className="text-ink-lo">STATUS</span>
        <span className={ac.isRunning ? "text-signal-cyan animate-pulse font-bold" : "text-ink-lo"}>
          {ac.isRunning ? `RUNNING · ${formatDuration(totalElapsed)}` : "IDLE"}
        </span>
      </div>

      {/* Live stage — shown when running */}
      {ac.isRunning && stageLabel && (
        <div className="border border-white/15 px-3 py-2 bg-signal-cyan/10">
          <div className="flex items-center justify-between gap-2">
            <span className="text-signal-cyan font-bold tracking-wider">{stageLabel}</span>
            <span className="text-signal-cyan shrink-0">
              {progressDone !== null && progressTotal !== null
                ? `${progressDone}/${progressTotal}`
                : (subStep ?? "")}
            </span>
          </div>
          {progressPct !== null && (
            <div className="mt-2 h-1 bg-white/[0.03] overflow-hidden">
              <div
                className="h-full bg-signal-cyan transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Last run results */}
      <div className="border-t border-white/[0.07] pt-2 space-y-1.5">
        <div className="flex justify-between">
          <span className="text-ink-lo">LAST RUN</span>
          <span className="text-ink">{formatTime(ac.lastCompletedAt)}</span>
        </div>
        {ac.lastElapsed > 0 && (
          <div className="flex justify-between">
            <span className="text-ink-lo">DURATION</span>
            <span className="text-ink">{formatDuration(ac.lastElapsed)}</span>
          </div>
        )}
        {(ac.lastIssuesCreated > 0 || ac.lastIssuesRetired > 0) && (
          <div className="flex justify-between">
            <span className="text-ink-lo">ISSUES</span>
            <span>
              <span className="text-ink-hi">+{ac.lastIssuesCreated} created</span>
              {ac.lastIssuesRetired > 0 && (
                <span className="text-ink-lo"> · -{ac.lastIssuesRetired} retired</span>
              )}
            </span>
          </div>
        )}
        {ac.lastStoriesGenerated > 0 && (
          <div className="flex justify-between">
            <span className="text-ink-lo">STORIES</span>
            <span className="text-ink-hi">{ac.lastStoriesGenerated} written</span>
          </div>
        )}
        {ac.lastBskyPosted > 0 && (
          <div className="flex justify-between">
            <span className="text-ink-lo">BLUESKY</span>
            <span className="text-signal-cyan">{ac.lastBskyPosted} posted</span>
          </div>
        )}
      </div>
    </div>
  );
}

const WINDOW_OPTIONS = [
  { value: 24, label: "24 RUNS" },
  { value: 72, label: "72 RUNS" },
  { value: 168, label: "168 RUNS" },
];

const counterLabel = (k: string) => k.replace(/_/g, " ");

export function ActionCenterDashboard({
  token,
  ac,
}: {
  token: string;
  ac: ActionRefreshState | null;
}) {
  const [limit, setLimit] = useState(72);
  const [metrics, setMetrics] = useState<ActionMetrics | null>(null);
  const [loadedLimit, setLoadedLimit] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminActionMetrics(token, limit)
      .then((m) => {
        if (!cancelled) setMetrics(m);
      })
      .catch(() => {
        if (!cancelled) setMetrics(null);
      })
      .finally(() => {
        if (!cancelled) setLoadedLimit(limit);
      });
    return () => {
      cancelled = true;
    };
  }, [token, limit]);

  const stale = loadedLimit !== limit;
  const runs = actionRunsOldestFirst(metrics?.runs ?? []);
  const labels = runs.map((r) => formatTime(r.recordedAt));
  // Runs are hourly, so a date alone repeats on every tick; keep the hour.
  const tickLabel = (i: number) => labels[i].replace(",", "");
  const count = (k: string) => runs.map((r) => r.counts[k] ?? 0);
  const published = runs.reduce((s, r) => s + r.issuesPublished, 0);
  const suppressed = metrics?.totals.suppressedTotal ?? 0;
  const fetched = metrics?.totals.intake.articles_fetched ?? 0;
  const suppressedEntries = Object.entries(metrics?.totals.suppressed ?? {})
    .map(([name, n]) => ({ name: counterLabel(name), count: n }))
    .sort((a, b) => b.count - a.count);
  const emptyMessage = metrics ? "No refresh runs recorded in this window." : "Loading…";

  return (
    <div className="space-y-6">
      <Segmented label="WINDOW" options={WINDOW_OPTIONS} value={limit} onChange={setLimit} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Refresh runs"
          value={runs.length.toLocaleString()}
          note="hourly; a gap is a run that crashed or never started"
        />
        <StatTile
          label="Articles fetched"
          value={formatCompact(fetched)}
          note={`over ${runs.length} runs`}
        />
        <StatTile
          label="Issues published"
          value={published.toLocaleString()}
          note="new topics + updates to existing"
        />
        <StatTile
          label="Suppressed by a gate"
          value={suppressed.toLocaleString()}
          note="dropped by a validator — see below"
        />
      </div>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <Panel title="Refresh status" live={!!ac?.isRunning}>
          <div className="-m-4">
            <ActionCenterStatus ac={ac} />
          </div>
        </Panel>
        <Panel title="Intake">
          <LineChart
            title="ARTICLES PER RUN"
            subtitle="fetched from feeds, and how many passed the policy-relevance filter"
            xLabels={labels}
            xTickLabel={tickLabel}
            series={[
              {
                key: "fetched",
                label: "Fetched",
                color: SERIES[0],
                values: count("articles_fetched"),
              },
              {
                key: "relevant",
                label: "Policy-relevant",
                color: SERIES[1],
                values: count("articles_policy_relevant"),
              },
            ]}
            formatValue={(v) => v.toLocaleString()}
            formatTick={formatCompact}
            stale={stale}
            emptyMessage={emptyMessage}
          />
        </Panel>
      </div>

      <Panel title="Output vs suppression">
        <p className="mb-4 text-xs font-mono text-ink-min">
          Every gate fails closed, so silence is the failure mode. Healthy intake with output near
          zero and suppression climbing is a validator dropping everything; intake near zero is a
          quiet news cycle or a broken feed.
        </p>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <LineChart
            title="ISSUES PUBLISHED VS SUPPRESSED, PER RUN"
            xLabels={labels}
            xTickLabel={tickLabel}
            series={[
              {
                key: "published",
                label: "Issues published",
                color: SERIES[0],
                values: runs.map((r) => r.issuesPublished),
              },
              {
                key: "suppressed",
                label: "Suppressed by a gate",
                color: SERIES[3],
                values: runs.map((r) => r.suppressed),
              },
            ]}
            formatValue={(v) => v.toLocaleString()}
            stale={stale}
            emptyMessage={emptyMessage}
          />
          <div>
            {suppressedEntries.length > 0 ? (
              <RankBars
                title="SUPPRESSED, BY GATE — WHOLE WINDOW"
                entries={suppressedEntries}
                unit="suppressed"
                labelWidth="w-48"
              />
            ) : (
              <p className="text-xs font-mono text-ink-min">Nothing suppressed in this window.</p>
            )}
          </div>
        </div>
      </Panel>
    </div>
  );
}
