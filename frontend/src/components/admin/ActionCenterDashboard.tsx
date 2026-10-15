"use client";

import { useEffect, useState } from "react";
import { useNow } from "@/hooks/useNow";
import {
  fetchAdminActionMetrics,
  fetchAdminActionTaps,
  fetchAdminActorConcentration,
  fetchAdminPipelineTrend,
  type ActionMetrics,
  type ActionTaps,
  type ActorConcentration,
  type ActionMetricsRun,
  type ActionRefreshState,
  type AdminPipelineStatus,
  type PipelineTrendRun,
} from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { formatCompact } from "./charts/scale";
import { formatDuration, formatTime, parseUTC } from "./format";
import { hourlySlots, slotStates, slotSum } from "./trends";
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
        No data yet: status available after first refresh.
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
        <span className={ac.isRunning ? "text-signal-cyan pulse-subtle font-bold" : "text-ink-lo"}>
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
  { value: 24, label: "24H" },
  { value: 72, label: "72H" },
  { value: 168, label: "7D" },
];

const hourLabel = (ms: number) =>
  new Date(ms).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

const counterLabel = (k: string) => k.replace(/_/g, " ");

/** Weekly principal-actor concentration of the published issues: is the
 *  feed drifting toward one person? */
const TAP_LABELS: Record<string, string> = {
  pick: "State picked",
  call: "Call",
  write: "Contact form",
  copy: "Script copied",
  comment: "Comment page",
  committee: "Committee",
  bill: "Bill",
};

/** Whether readers use an issue's action links: taps per day and kind, and
 *  the issues tapped most. Counts only (backend ActionTap): nothing says who
 *  tapped, from where, or which member or state. */
export function ActionTapsPanel({ token }: { token: string }) {
  const [data, setData] = useState<ActionTaps | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchAdminActionTaps(token, 30)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const days = data?.days ?? [];
  const kinds = data?.kinds ?? [];
  const contacts = (r: { call: number; write: number }) => r.call + r.write;
  return (
    <Panel title="Action taps">
      <p className="mb-4 text-xs font-mono text-ink-min">
        Taps on an issue&apos;s action links over 30 days, counted per day, issue and kind and
        nothing else: no address, hash or browser, and not which state, member or number. A state
        pick followed by no call is a reader who looked and stopped.
      </p>
      {failed ? (
        <p className="text-xs font-mono text-signal-amber">Couldn&apos;t load action taps.</p>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <LineChart
            title="STATE PICKS AND CONTACTS PER DAY"
            subtitle="contacts = calls + contact forms"
            xLabels={days.map((d) => d.date.slice(5))}
            series={[
              {
                key: "pick",
                label: "State picked",
                color: SERIES[1],
                values: days.map((d) => d.pick),
              },
              { key: "contact", label: "Contacts", color: SERIES[0], values: days.map(contacts) },
            ]}
            formatValue={(v) => v.toLocaleString()}
            emptyMessage={data ? "No taps in this window." : "Loading…"}
          />
          <div className="space-y-6">
            <RankBars
              title="BY KIND · 30 DAYS"
              entries={kinds.map((k) => ({ name: TAP_LABELS[k] ?? k, count: data!.totals[k] }))}
              unit="taps"
              labelWidth="w-32"
            />
            <RankBars
              title="ISSUES BY CONTACTS · 30 DAYS"
              entries={(data?.issues ?? [])
                .map((i) => ({ name: i.title ?? i.publicId, count: contacts(i) }))
                .filter((e) => e.count > 0)}
              unit="contacts"
              labelWidth="w-48"
            />
          </div>
        </div>
      )}
    </Panel>
  );
}

export function ActorConcentrationPanel({ token }: { token: string }) {
  const [data, setData] = useState<ActorConcentration | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchAdminActorConcentration(token, 12)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const weeks = data?.weeks ?? [];
  const current = weeks[weeks.length - 1];
  const labels = weeks.map((w) => `wk of ${w.weekStart.slice(5)}`);
  const share = (n: number, of: number) => (of ? n / of : null);
  return (
    <Panel title="Who leads the issues">
      <p className="mb-4 text-xs font-mono text-ink-min">
        Each published news issue&apos;s principal actor is the official named first in its
        headline, or the kind of story when it names none. The Herfindahl index is the sum of
        squared shares: 1.0 when one actor leads every issue of the week, lower as the lead spreads.
        No selection rule acts on it: a daily cap per person and demoting stories with no government
        actor were both measured and removed real civic stories with nothing to replace them, so
        this is watched rather than enforced.
      </p>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <LineChart
          title="ACTOR CONCENTRATION PER WEEK"
          subtitle="Herfindahl index of principal actors, and the share of issues the president leads"
          xLabels={labels}
          series={[
            {
              key: "hhi",
              label: "Herfindahl index",
              color: SERIES[0],
              values: weeks.map((w) => w.hhi),
            },
            {
              key: "president",
              label: "President's share",
              color: SERIES[1],
              values: weeks.map((w) => share(w.president, w.issues)),
            },
            {
              key: "nogov",
              label: "No government actor",
              color: SERIES[3],
              values: weeks.map((w) => share(w.noGovernment, w.issues)),
            },
          ]}
          formatValue={(v) => v.toFixed(2)}
          emptyMessage={
            data ? "No labelled issues yet." : failed ? "Couldn't load concentration." : "Loading…"
          }
        />
        <div className="text-xs font-mono">
          {current && current.top ? (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
              <dt className="text-ink-lo">THIS WEEK</dt>
              <dd className="text-ink-hi tabular-nums">{current.issues} issues</dd>
              <dt className="text-ink-lo">HERFINDAHL</dt>
              <dd className="text-ink-hi tabular-nums">{current.hhi?.toFixed(2) ?? "—"}</dd>
              <dt className="text-ink-lo">MOST FREQUENT</dt>
              <dd className="text-ink-hi">
                {current.top.name} ({current.top.issues})
              </dd>
              <dt className="text-ink-lo">NO GOVERNMENT ACTOR</dt>
              <dd className="text-ink-hi tabular-nums">{current.noGovernment}</dd>
            </dl>
          ) : (
            <p className="text-ink-min">No issues this week yet.</p>
          )}
        </div>
      </div>
    </Panel>
  );
}

export function ActionCenterDashboard({
  token,
  ac,
  status,
  processStartedAt,
}: {
  token: string;
  ac: ActionRefreshState | null;
  /** Live pipeline flags — what the scheduler itself checks before a refresh. */
  status: AdminPipelineStatus | null;
  processStartedAt: string | null;
}) {
  const [limit, setLimit] = useState(72); // hours
  const [metrics, setMetrics] = useState<ActionMetrics | null>(null);
  const [loadedLimit, setLoadedLimit] = useState<number | null>(null);
  // The slots are anchored to when the data was fetched, not a live clock:
  // the window and the rows it is filled from always describe the same
  // moment, and nothing re-renders every second for a value that only
  // changes hourly. Refetched every few minutes so a new hour's run lands.
  const [fetchedAt, setFetchedAt] = useState(0);
  // Set when the latest refetch failed. The last good data stays up: a
  // transient error (a backend mid-rollout) must not redraw three healthy
  // days as "no refresh runs", which reads as the Action Center being dead.
  const [refetchFailed, setRefetchFailed] = useState(false);

  // Senate/House runs over the same window: the scheduler skips a refresh
  // while one is running, and those slots are not failures. Null when the
  // lookup failed, in which case empty slots can't be classified.
  const lastRefreshCompletedAt = ac?.lastCompletedAt ?? null;
  // Changes when a pipeline the scheduler waits on starts or finishes, so
  // the rows are refetched then: a finished run's row must replace the
  // "running" one it was fetched as.
  const blockingFlags = [
    status?.isRunning,
    status?.houseIsRunning,
    status?.supplementaryIsRunning,
    status?.stockTradesIsRunning,
  ]
    .map((f) => (f ? "1" : "0"))
    .join("");
  const [pipelineRuns, setPipelineRuns] = useState<PipelineTrendRun[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      Promise.all([
        // Two hours of slack: the backend's window is rolling, the chart's is
        // slot-aligned, so fetch a superset and let the slots decide.
        fetchAdminActionMetrics(token, limit + 2),
        fetchAdminPipelineTrend(token, Math.ceil((limit + 2) / 24) + 1)
          .then((t) => t.runs)
          .catch(() => null),
      ])
        .then(([m, p]) => {
          if (cancelled) return;
          setMetrics(m);
          setPipelineRuns(p);
          setLoadedLimit(limit);
          setFetchedAt(Date.now());
          setRefetchFailed(false);
        })
        .catch(() => {
          if (!cancelled) setRefetchFailed(true);
        });
    load();
    const id = setInterval(load, 5 * 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // lastCompletedAt changes the moment a refresh finishes (the status poll
    // sees it within seconds), so its new row is fetched then rather than up
    // to five minutes later — until it lands, a refresh that ran past its
    // slot would read as a missed hour. blockingFlags does the same for a
    // pipeline run starting or finishing.
  }, [token, limit, lastRefreshCompletedAt, blockingFlags]);

  const stale = loadedLimit !== limit;
  const hours = loadedLimit ?? limit;
  const slots = fetchedAt ? hourlySlots(metrics?.runs ?? [], hours, fetchedAt) : [];
  // Every figure on this tab is computed from these slots — the tiles, the
  // lines and the per-gate bars — so they all describe the same set of runs.
  const states = slotStates(slots, pipelineRuns ?? [], fetchedAt, {
    runningNow: {
      house: !!status?.houseIsRunning,
      supplementary: !!status?.supplementaryIsRunning,
      stock_trades: !!status?.stockTradesIsRunning,
    },
    processStartedAt: processStartedAt ? parseUTC(processStartedAt).getTime() : null,
    refreshStartedAt: ac?.isRunning && ac.startedAt ? parseUTC(ac.startedAt).getTime() : null,
  });
  const ran = states.filter((st) => st === "ran").length;
  const skipped = states.filter((st) => st === "skipped").length;
  const missing = states.filter((st) => st === "missing").length;
  const runCount = slots.reduce((n, s) => n + s.runs.length, 0);
  const loaded = fetchedAt > 0;
  const labels = slots.map((s) => hourLabel(s.start));
  const tickLabel = (i: number) => labels[i].replace(",", "");
  const count = (k: string) => slots.map((s) => slotSum(s, (r) => r.counts[k] ?? 0));
  const total = (pick: (r: ActionMetricsRun) => number) =>
    slots.reduce((sum, s) => sum + (slotSum(s, pick) ?? 0), 0);
  const published = total((r) => r.issuesPublished);
  const suppressed = total((r) => r.suppressed);
  const fetched = total((r) => r.counts.articles_fetched ?? 0);
  // Which counters are suppression gates is the backend's grouping; the
  // counts are re-summed over the charted slots only.
  const suppressedEntries = Object.keys(metrics?.totals.suppressed ?? {})
    .map((name) => ({ name: counterLabel(name), count: total((r) => r.counts[name] ?? 0) }))
    .filter((e) => e.count > 0)
    .sort((a, b) => b.count - a.count);
  const emptyMessage = metrics
    ? "No refresh runs recorded in this window."
    : refetchFailed
      ? "Couldn't load Action Center metrics."
      : "Loading…";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <Segmented label="WINDOW" options={WINDOW_OPTIONS} value={limit} onChange={setLimit} />
        {refetchFailed && (
          <span role="status" className="text-xs font-mono text-signal-amber">
            Couldn&apos;t refresh: showing data from {fetchedAt ? hourLabel(fetchedAt) : "—"}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Refreshes"
          value={loaded ? `${ran} / ${ran + missing}` : "—"}
          tone={loaded && missing > 0 && pipelineRuns ? "text-signal-amber" : "text-ink-hi"}
          note={
            !loaded
              ? undefined
              : !pipelineRuns
                ? "couldn't load pipeline runs, so a skipped hour can't be told from a missed one"
                : `${missing} missed (crashed or never started) · ${skipped} skipped while a pipeline or a slow refresh ran`
          }
        />
        <StatTile
          label="Articles fetched"
          value={loaded ? formatCompact(fetched) : "—"}
          note={loaded ? `over ${runCount} runs` : undefined}
        />
        <StatTile
          label="Issues published"
          value={loaded ? published.toLocaleString() : "—"}
          note="new topics + updates to existing"
        />
        <StatTile
          label="Suppressed by a gate"
          value={loaded ? suppressed.toLocaleString() : "—"}
          note="dropped by a validator: see below"
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
            subtitle="fetched, and how many passed the policy-relevance filter; a gap is an hour with no refresh"
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
                values: slots.map((s) => slotSum(s, (r) => r.issuesPublished)),
              },
              {
                key: "suppressed",
                label: "Suppressed by a gate",
                color: SERIES[3],
                values: slots.map((s) => slotSum(s, (r) => r.suppressed)),
              },
            ]}
            formatValue={(v) => v.toLocaleString()}
            stale={stale}
            emptyMessage={emptyMessage}
          />
          <div>
            {suppressedEntries.length > 0 ? (
              <RankBars
                title="SUPPRESSED BY GATE · THIS WINDOW"
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

      <ActionTapsPanel token={token} />

      <ActorConcentrationPanel token={token} />
    </div>
  );
}
