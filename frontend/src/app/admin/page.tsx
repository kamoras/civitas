"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNow } from "@/hooks/useNow";
import { useSessionToken } from "@/hooks/useSessionToken";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import {
  adminAuth,
  fetchAdminDashboard,
  fetchAdminPipelineStatus,
  fetchAdminPipelineHistory,
  fetchAdminSystemStats,
  fetchAdminVisitorStats,
  fetchAdminVisitorBreakdown,
  fetchAdminTopPages,
  fetchAdminPipelineTimings,
  setPoliticianVacancy,
  clearStuckHousePipeline,
  clearStuckStockTradesPipeline,
  clearStuckSupplementaryPipeline,
  type AdminDashboard,
  type AdminPipelineStatus,
  type ActionRefreshState,
  type HostStats,
  type PipelineHistoryRun,
  type PipelineStepInfo,
  type UptimeInfo,
  type VisitorStatsDay,
  type VisitorBreakdown,
  type TopPageEntry,
  type PipelineTimings,
} from "@/lib/api";
import { cacheHitRate, describeRun } from "@/lib/pipelineRuns";

const PHASE_LABELS: Record<string, string> = {
  fetch: "FETCHING DATA",
  transform: "TRANSFORMING",
  analyze: "ANALYZING",
  explore: "EXPLORE DOCS",
  justices: "SCOTUS",
  presidents: "PRESIDENTS",
  finalize: "FINALIZING",
};

const TOKEN_KEY = "civitas_admin_token";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatRate(bytesPerSec: number): string {
  if (bytesPerSec < 1024) return `${Math.round(bytesPerSec)} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  return `${(bytesPerSec / (1024 * 1024)).toFixed(1)} MB/s`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function parseUTC(iso: string): Date {
  if (iso.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(iso)) return new Date(iso);
  return new Date(iso + "Z");
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = parseUTC(iso);
    return d.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block w-2 h-2 ${ok ? "bg-phos" : "bg-signal-magenta"}`}
      aria-label={ok ? "Healthy" : "Unhealthy"}
    />
  );
}

// --- Login Screen ---
function LoginScreen({ onLogin }: { onLogin: (token: string) => void }) {
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    setLoading(true);
    setError("");
    const ok = await adminAuth(input.trim());
    if (ok) {
      onLogin(input.trim());
    } else {
      setError("Invalid token");
    }
    setLoading(false);
  };

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="min-h-screen bg-surface-base flex items-center justify-center px-4"
    >
      <div className="w-full max-w-md">
        <div className="panel">
          <TerminalTitlebar title="Sign in" />
          <div className="p-6">
            <h1 className="font-mono text-sm text-ink-hi tracking-widest mb-6 text-center">
              CIVITAS ADMIN
            </h1>
            <form onSubmit={handleSubmit}>
              <label htmlFor="admin-token" className="block text-ink-lo text-xs font-mono mb-2">
                ADMIN TOKEN:
              </label>
              <input
                id="admin-token"
                type="password"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Enter admin token..."
                className="w-full bg-transparent border border-white/15 px-3 py-2
                           text-ink-hi text-sm font-mono placeholder:text-ink-min
                           outline-none focus:border-phos/40"
                autoFocus
                aria-invalid={!!error}
                aria-describedby={error ? "admin-token-error" : undefined}
              />
              {error && (
                <p id="admin-token-error" className="text-signal-magenta text-xs mt-2" role="alert">
                  {error}
                </p>
              )}
              <button
                type="submit"
                disabled={loading || !input.trim()}
                // Disabled dims the FILL, not just the label. It used to keep
                // `bg-phos` and only drop the text to `text-ink-lo`, which put
                // a mid grey-green on full phosphor at 1.96:1 — the first thing
                // an operator sees, with an unreadable word on it. WCAG exempts
                // inactive controls from contrast, so nothing flagged it; it
                // still looked like a rendering fault.
                className="mt-4 w-full border border-transparent bg-phos py-2 font-mono text-xs text-surface-base transition-colors hover:bg-signal-cyan disabled:border-white/15 disabled:bg-transparent disabled:text-ink-min"
              >
                {loading ? "AUTHENTICATING..." : "AUTHENTICATE"}
              </button>
            </form>
          </div>
        </div>
      </div>
    </main>
  );
}

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
function PipelineProgressBar({
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

// --- Usage Bar ---
function UsageBar({
  pct,
  warnAt = 75,
  critAt = 90,
  ariaLabel,
}: {
  pct: number;
  warnAt?: number;
  critAt?: number;
  ariaLabel: string;
}) {
  const color = pct >= critAt ? "bg-signal-magenta" : pct >= warnAt ? "bg-signal-amber" : "bg-phos";
  const value = Math.min(Math.round(pct), 100);
  return (
    <div
      className="w-full h-1.5 bg-white/[0.03] overflow-hidden"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={ariaLabel}
    >
      <div
        className={`h-full ${color} transition-all duration-700`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// --- Uptime Tracker ---
function UptimeTracker({
  uptime,
  hostUptime,
}: {
  uptime?: UptimeInfo;
  hostUptime?: number | null;
}) {
  const now = useNow();

  const processStart = uptime?.processStartedAt
    ? parseUTC(uptime.processStartedAt).getTime()
    : null;
  const appUptimeSec = processStart ? Math.max(0, Math.floor((now - processStart) / 1000)) : null;

  const firstRun = uptime?.firstPipelineRun ? parseUTC(uptime.firstPipelineRun).getTime() : null;
  const totalServiceDays = firstRun ? Math.max(1, Math.floor((now - firstRun) / 86400000)) : null;

  function tickingUptime(seconds: number): string {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = (n: number) => String(n).padStart(2, "0");
    if (d > 0) return `${d}d ${pad(h)}:${pad(m)}:${pad(s)}`;
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
  }

  return (
    <div className="panel mb-6">
      <TerminalTitlebar title="Uptime">
        <span className="ml-auto text-ink-lo text-xs font-mono mr-2">
          live
          <span className="inline-block w-1.5 h-1.5 bg-phos ml-1 animate-pulse" />
        </span>
      </TerminalTitlebar>
      <div className="p-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* App Uptime — large ticking counter */}
          <div className="sm:col-span-2">
            <div className="text-xs font-mono text-ink-lo tracking-wider mb-2">
              APPLICATION UPTIME
            </div>
            <div className="font-mono text-2xl sm:text-3xl text-signal-cyan tabular-nums tracking-wider">
              {appUptimeSec != null ? tickingUptime(appUptimeSec) : "—"}
            </div>
            <div className="text-xs font-mono text-ink-min mt-1.5">
              {uptime?.processStartedAt
                ? `started ${formatTime(uptime.processStartedAt)}`
                : "unknown start time"}
            </div>
          </div>

          {/* Sidebar stats */}
          <div className="space-y-3">
            <div>
              <div className="text-xs font-mono text-ink-lo tracking-wider mb-0.5">HOST UPTIME</div>
              <div className="font-mono text-sm text-ink-hi tabular-nums">
                {hostUptime != null ? tickingUptime(hostUptime) : "—"}
              </div>
            </div>
            <div>
              <div className="text-xs font-mono text-ink-lo tracking-wider mb-0.5">SERVICE AGE</div>
              <div className="font-mono text-sm text-ink-hi">
                {totalServiceDays != null
                  ? `${totalServiceDays} day${totalServiceDays !== 1 ? "s" : ""}`
                  : "—"}
              </div>
              <div className="text-xs font-mono text-ink-min mt-0.5">
                {uptime?.firstPipelineRun ? `since ${formatTime(uptime.firstPipelineRun)}` : ""}
              </div>
            </div>
          </div>
        </div>

        {/* Uptime bar visualization */}
        {appUptimeSec != null && (
          <div className="mt-4 pt-3 border-t border-white/[0.07]">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-mono text-ink-min">SESSION HEALTH</span>
              <span className="text-xs font-mono text-ink-lo tabular-nums">
                {appUptimeSec >= 86400
                  ? `${Math.floor(appUptimeSec / 86400)}d`
                  : appUptimeSec >= 3600
                    ? `${Math.floor(appUptimeSec / 3600)}h`
                    : `${Math.floor(appUptimeSec / 60)}m`}{" "}
                since last deploy
              </span>
            </div>
            <div
              className="w-full h-2 bg-white/[0.03] overflow-hidden"
              role="progressbar"
              aria-valuenow={Math.min(100, Math.round((appUptimeSec / 86400) * 100))}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Session health since last deploy"
            >
              <div
                className="h-full bg-signal-cyan transition-all duration-1000"
                style={{
                  width: `${Math.min(100, (appUptimeSec / 86400) * 100)}%`,
                }}
              />
            </div>
            <div className="flex justify-between text-xs font-mono text-ink-min mt-0.5">
              <span>0h</span>
              <span>6h</span>
              <span>12h</span>
              <span>18h</span>
              <span>24h</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// --- System Monitor ---
function SystemMonitor({ token, initialStats }: { token: string; initialStats?: HostStats }) {
  const [stats, setStats] = useState<HostStats | null>(initialStats ?? null);
  const [netRate, setNetRate] = useState<{ rx: number; tx: number } | null>(null);
  const prevNetRef = useRef<{ rx: number; tx: number; time: number } | null>(null);
  useEffect(() => {
    const poll = async () => {
      try {
        const s = await fetchAdminSystemStats(token);
        setStats(s);

        const now = Date.now();
        if (s.netRxBytes != null && s.netTxBytes != null) {
          const prev = prevNetRef.current;
          if (prev) {
            const dt = (now - prev.time) / 1000;
            if (dt > 0) {
              setNetRate({
                rx: Math.max(0, (s.netRxBytes - prev.rx) / dt),
                tx: Math.max(0, (s.netTxBytes - prev.tx) / dt),
              });
            }
          }
          prevNetRef.current = { rx: s.netRxBytes, tx: s.netTxBytes, time: now };
        }
      } catch {}
    };
    if (!initialStats) poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [token, initialStats]);

  if (!stats) return null;

  const tempColor =
    stats.cpuTempC == null
      ? ""
      : stats.cpuTempC >= 80
        ? "text-signal-magenta"
        : stats.cpuTempC >= 65
          ? "text-signal-amber"
          : "text-ink-hi";

  const loadPct = stats.loadAvg ? Math.round((stats.loadAvg[0] / stats.cpuCount) * 100) : 0;

  return (
    <div className="panel mb-6">
      <TerminalTitlebar title="System monitor">
        <span className="ml-auto text-ink-lo text-xs font-mono mr-2">
          live
          <span className="inline-block w-1.5 h-1.5 bg-phos ml-1 animate-pulse" />
        </span>
      </TerminalTitlebar>
      <div className="p-4">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          {/* CPU Load */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-ink-lo text-xs font-mono tracking-wider">CPU LOAD</span>
              <span className="text-ink-hi text-xs font-mono tabular-nums">
                {stats.loadAvg ? `${stats.loadAvg[0].toFixed(2)} / ${stats.cpuCount}` : "—"}
              </span>
            </div>
            <UsageBar pct={loadPct} ariaLabel="CPU load percentage" />
            <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
              {stats.loadAvg
                ? `${stats.loadAvg[0].toFixed(1)} · ${stats.loadAvg[1].toFixed(1)} · ${stats.loadAvg[2].toFixed(1)}`
                : ""}
            </div>
          </div>

          {/* Memory */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-ink-lo text-xs font-mono tracking-wider">MEMORY</span>
              <span className="text-ink-hi text-xs font-mono tabular-nums">
                {stats.memUsedPct}%
              </span>
            </div>
            <UsageBar pct={stats.memUsedPct} ariaLabel="Memory usage percentage" />
            <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
              {formatBytes(stats.memUsedBytes)} / {formatBytes(stats.memTotalBytes)}
            </div>
          </div>

          {/* Disk */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-ink-lo text-xs font-mono tracking-wider">DISK</span>
              <span className="text-ink-hi text-xs font-mono tabular-nums">
                {stats.diskUsedPct}%
              </span>
            </div>
            <UsageBar
              pct={stats.diskUsedPct}
              warnAt={80}
              critAt={95}
              ariaLabel="Disk usage percentage"
            />
            <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
              {formatBytes(stats.diskFreeBytes)} free
            </div>
          </div>

          {/* Temperature + Uptime */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-ink-lo text-xs font-mono tracking-wider">CPU TEMP</span>
              <span className={`text-xs font-mono tabular-nums ${tempColor}`}>
                {stats.cpuTempC != null ? `${stats.cpuTempC}°C` : "—"}
              </span>
            </div>
            {stats.cpuTempC != null && (
              <UsageBar
                pct={(stats.cpuTempC / 85) * 100}
                warnAt={76}
                critAt={94}
                ariaLabel="CPU temperature"
              />
            )}
            <div className="text-xs text-ink-min font-mono mt-1">
              uptime {formatUptime(stats.uptimeSeconds)}
            </div>
          </div>

          {/* Network */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-ink-lo text-xs font-mono tracking-wider">NETWORK</span>
              <span className="text-ink-hi text-xs font-mono tabular-nums">
                {netRate ? formatRate(netRate.rx + netRate.tx) : "—"}
              </span>
            </div>
            <div className="space-y-1.5">
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-ink-min font-mono">▼ RX</span>
                  <span className="text-xs text-ink-lo font-mono tabular-nums">
                    {netRate ? formatRate(netRate.rx) : "—"}
                  </span>
                </div>
                {netRate && (
                  <div
                    className="w-full h-1 bg-white/[0.03] mt-0.5"
                    role="progressbar"
                    aria-valuenow={Math.min(100, Math.round((netRate.rx / (1024 * 1024)) * 10))}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Network receive rate"
                  >
                    <div
                      className="h-full bg-phos transition-all duration-500"
                      style={{ width: `${Math.min(100, (netRate.rx / (1024 * 1024)) * 10)}%` }}
                    />
                  </div>
                )}
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-ink-min font-mono">▲ TX</span>
                  <span className="text-xs text-ink-lo font-mono tabular-nums">
                    {netRate ? formatRate(netRate.tx) : "—"}
                  </span>
                </div>
                {netRate && (
                  <div
                    className="w-full h-1 bg-white/[0.03] mt-0.5"
                    role="progressbar"
                    aria-valuenow={Math.min(100, Math.round((netRate.tx / (1024 * 1024)) * 10))}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Network transmit rate"
                  >
                    <div
                      className="h-full bg-signal-cyan/50 transition-all duration-500"
                      style={{ width: `${Math.min(100, (netRate.tx / (1024 * 1024)) * 10)}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
            <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
              {stats.netRxBytes != null
                ? `↓${formatBytes(stats.netRxBytes)} ↑${formatBytes(stats.netTxBytes ?? 0)}`
                : ""}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- Seat Vacancy Control ---
const VACANCY_REASONS = ["deceased", "resigned", "expelled"] as const;

function VacancyControl({ token }: { token: string }) {
  const [politicianId, setPoliticianId] = useState("");
  const [action, setAction] = useState<"vacate" | "restore">("vacate");
  const [reason, setReason] = useState<(typeof VACANCY_REASONS)[number]>("deceased");
  const [leftOfficeDate, setLeftOfficeDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!politicianId.trim()) return;
    setSubmitting(true);
    setResult(null);
    setError(null);
    try {
      const res = await setPoliticianVacancy(
        token,
        politicianId.trim(),
        action === "restore",
        action === "vacate" ? reason : undefined,
        action === "vacate" && leftOfficeDate ? leftOfficeDate : undefined
      );
      setResult(
        action === "vacate"
          ? `${res.name}'s seat marked vacant (${res.vacancyReason}).`
          : `${res.name}'s seat restored to current.`
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="panel mb-6">
      <TerminalTitlebar title="Seat vacancies" />
      <div className="p-4 space-y-3">
        <p className="text-ink-min text-xs font-mono">
          Marks a senator/representative&apos;s seat vacant (or restores it) without deleting their
          historical data. No automated detection — this is manual only.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-ink-lo text-xs font-mono tracking-wider">POLITICIAN ID</label>
            <input
              value={politicianId}
              onChange={(e) => setPoliticianId(e.target.value)}
              placeholder="e.g. lindsey-graham"
              className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 w-48 focus:outline-none focus:border-phos/40"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-ink-lo text-xs font-mono tracking-wider">ACTION</label>
            <select
              value={action}
              onChange={(e) => setAction(e.target.value as "vacate" | "restore")}
              className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 focus:outline-none focus:border-phos/40"
            >
              <option value="vacate">Mark vacant</option>
              <option value="restore">Restore to current</option>
            </select>
          </div>
          {action === "vacate" && (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-ink-lo text-xs font-mono tracking-wider">REASON</label>
                <select
                  value={reason}
                  onChange={(e) => setReason(e.target.value as (typeof VACANCY_REASONS)[number])}
                  className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 focus:outline-none focus:border-phos/40"
                >
                  {VACANCY_REASONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-ink-lo text-xs font-mono tracking-wider">LEFT OFFICE</label>
                <input
                  type="date"
                  value={leftOfficeDate}
                  onChange={(e) => setLeftOfficeDate(e.target.value)}
                  className="bg-surface border border-white/[0.07] text-ink-hi text-xs font-mono px-2 py-1.5 focus:outline-none focus:border-phos/40"
                />
              </div>
            </>
          )}
          <button
            onClick={submit}
            disabled={submitting || !politicianId.trim()}
            className="font-mono text-xs text-signal-cyan border border-white/15 px-3 py-1.5 hover:bg-signal-cyan/10 transition-colors disabled:opacity-40"
          >
            {submitting ? "SUBMITTING..." : "SUBMIT"}
          </button>
        </div>
        {result && <p className="text-ink-hi text-xs font-mono">{result}</p>}
        {error && <p className="text-signal-magenta text-xs font-mono">{error}</p>}
      </div>
    </div>
  );
}

// --- Visitor Stats ---
function BreakdownGroup({
  title,
  entries,
}: {
  title: string;
  entries: { name: string; count: number }[];
}) {
  if (entries.length === 0) return null;
  const max = Math.max(1, ...entries.map((e) => e.count));
  return (
    <div>
      <div className="text-ink-min text-xs font-mono tracking-wider mb-1.5">{title}</div>
      <div className="space-y-1">
        {entries.map((e) => (
          <div key={e.name} className="flex items-center gap-2">
            <span className="text-ink-lo text-xs font-mono w-14 shrink-0 truncate">{e.name}</span>
            <div className="flex-1">
              <UsageBar
                pct={(e.count / max) * 100}
                warnAt={101}
                critAt={101}
                ariaLabel={`${e.count} visitors used ${e.name}`}
              />
            </div>
            <span className="text-ink-lo text-xs font-mono tabular-nums w-6 text-right shrink-0">
              {e.count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

const TIMING_KINDS: { kind: string; label: string }[] = [
  { kind: "pipeline_runs", label: "SENATE" },
  { kind: "house_pipeline_runs", label: "HOUSE" },
  { kind: "supplementary_pipeline_runs", label: "SUPP" },
  { kind: "stock_trades_pipeline_runs", label: "STOCK" },
  { kind: "election_pipeline_runs", label: "ELECTION" },
];

/** Per-phase duration breakdown for recent runs.
 *
 * The run history already shows total elapsed time, which says a run got
 * slower but not where. This splits each run by the fetch/transform/
 * analyze/finalize tag on its steps — the split that separates waiting on
 * rate-limited external APIs from local compute.
 */
function PipelinePhaseTimings({ token }: { token: string }) {
  const [kind, setKind] = useState<string>("pipeline_runs");
  const [timings, setTimings] = useState<PipelineTimings | null>(null);
  // Which kind the current `timings` belongs to. Loading is derived from
  // this rather than set synchronously inside the effect — the latter
  // trips react-hooks/set-state-in-effect and causes a cascading render.
  const [loadedKind, setLoadedKind] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const loading = loadedKind !== kind;

  useEffect(() => {
    let cancelled = false;
    fetchAdminPipelineTimings(token, kind, 10)
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
  const maxTotal = Math.max(1, ...runs.map((r) => r.totalSeconds));

  return (
    <div className="panel mb-6">
      <TerminalTitlebar title="Phase timings" />
      <div className="p-4">
        <div className="flex flex-wrap gap-1.5 mb-4">
          {TIMING_KINDS.map((k) => (
            <button
              key={k.kind}
              type="button"
              onClick={() => setKind(k.kind)}
              aria-pressed={kind === k.kind}
              className={`px-2 py-1 text-xs font-mono tracking-wider border transition-colors ${
                kind === k.kind
                  ? "border-phos/40 text-ink-hi"
                  : "border-white/[0.07] text-ink-min hover:text-phos"
              }`}
            >
              {k.label}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="text-ink-min text-xs font-mono">Loading…</div>
        ) : runs.length === 0 ? (
          <div className="text-ink-min text-xs font-mono">
            No phase timings recorded yet — they are written as each run completes its steps.
          </div>
        ) : (
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
                      #{run.runId}
                      {run.startedAt ? ` · ${run.startedAt.slice(5, 16).replace("T", " ")}` : ""}
                    </span>
                    <span className="text-ink text-xs font-mono tabular-nums shrink-0">
                      {formatDuration(run.totalSeconds)}
                      {run.blockedPct > 0 && (
                        // Share of the run spent inside a rate limiter. A run
                        // that is mostly blocked is throughput-bound on an
                        // external API and will not get shorter on faster
                        // local hardware.
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
                      comparable to each other, not just internally. */}
                  <div
                    className="flex w-full h-2 bg-white/[0.03] overflow-hidden"
                    role="img"
                    aria-label={run.phases.map((p) => `${p.phase} ${p.pct}%`).join(", ")}
                    style={{ width: `${(run.totalSeconds / maxTotal) * 100}%` }}
                  >
                    {run.phases.map((p, i) => (
                      <div
                        key={p.phase}
                        className={
                          ["bg-phos", "bg-signal-amber", "bg-signal-magenta", "bg-phos"][i % 4]
                        }
                        style={{ width: `${p.pct}%` }}
                        title={`${p.phase}: ${formatDuration(p.seconds)} (${p.pct}%)`}
                      />
                    ))}
                  </div>
                </button>

                {expanded === run.runId && (
                  <div className="mt-2 pl-2 border-l border-white/[0.07] space-y-1">
                    {run.phases.map((p) => (
                      <div key={p.phase} className="flex justify-between gap-3">
                        <span className="text-ink-lo text-xs font-mono">
                          {PHASE_LABELS[p.phase] ?? p.phase.toUpperCase()} ({p.steps})
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
        )}
      </div>
    </div>
  );
}

function VisitorStats({ token }: { token: string }) {
  const [days, setDays] = useState<VisitorStatsDay[]>([]);
  const [breakdown, setBreakdown] = useState<VisitorBreakdown | null>(null);
  const [topPages, setTopPages] = useState<TopPageEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetchAdminVisitorStats(token, 14),
      fetchAdminVisitorBreakdown(token),
      fetchAdminTopPages(token, 7),
    ])
      .then(([d, b, p]) => {
        setDays(d);
        setBreakdown(b);
        setTopPages(p);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) return null;

  const today = new Date().toISOString().slice(0, 10);
  const todayCount = days.find((d) => d.date === today)?.uniqueVisitors ?? 0;
  const maxCount = Math.max(1, ...days.map((d) => d.uniqueVisitors));

  return (
    <div className="panel mb-6">
      <TerminalTitlebar title="Visits" />
      <div className="p-4">
        <div className="flex items-baseline justify-between mb-4">
          <span className="text-ink-lo text-xs font-mono tracking-wider">
            UNIQUE VISITORS TODAY
          </span>
          <span className="text-ink-hi text-2xl font-mono tabular-nums">{todayCount}</span>
        </div>
        {days.length === 0 ? (
          <div className="text-ink-min text-xs font-mono">No visitor data yet.</div>
        ) : (
          <div className="space-y-1.5">
            {days.map((d) => (
              <div key={d.date} className="flex items-center gap-3">
                <span className="text-ink-min text-xs font-mono tabular-nums w-16 shrink-0">
                  {d.date.slice(5)}
                </span>
                <div className="flex-1">
                  <UsageBar
                    pct={(d.uniqueVisitors / maxCount) * 100}
                    warnAt={101}
                    critAt={101}
                    ariaLabel={`${d.uniqueVisitors} unique visitors on ${d.date}`}
                  />
                </div>
                <span className="text-ink-lo text-xs font-mono tabular-nums w-8 text-right shrink-0">
                  {d.uniqueVisitors}
                </span>
              </div>
            ))}
          </div>
        )}
        {breakdown &&
          (breakdown.browsers.length > 0 ||
            breakdown.os.length > 0 ||
            breakdown.devices.length > 0) && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4 pt-4 border-t border-white/[0.07]">
              <BreakdownGroup title="BROWSER — TODAY" entries={breakdown.browsers} />
              <BreakdownGroup title="OS — TODAY" entries={breakdown.os} />
              <BreakdownGroup title="DEVICE — TODAY" entries={breakdown.devices} />
            </div>
          )}
        {topPages.length > 0 && (
          <div className="mt-4 pt-4 border-t border-white/[0.07]">
            <div className="text-ink-min text-xs font-mono tracking-wider mb-1.5">
              MOST VISITED PAGES — LAST 7 DAYS
            </div>
            <div className="space-y-1">
              {topPages.map((p) => {
                const max = Math.max(1, ...topPages.map((e) => e.views));
                return (
                  <div key={p.path} className="flex items-center gap-2">
                    <span className="text-ink-lo text-xs font-mono w-40 shrink-0 truncate">
                      {p.path}
                    </span>
                    <div className="flex-1">
                      <UsageBar
                        pct={(p.views / max) * 100}
                        warnAt={101}
                        critAt={101}
                        ariaLabel={`${p.views} views on ${p.path}`}
                      />
                    </div>
                    <span className="text-ink-lo text-xs font-mono tabular-nums w-10 text-right shrink-0">
                      {p.views}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <p className="text-ink-min text-xs font-mono mt-3">
          Counted by a salted, daily-rotating hash — no IP addresses are stored. Browser/OS/device
          are coarse categories only, never the raw User-Agent string. Page views are raw counts
          (not deduped by visitor) grouped by route, e.g. all politician profiles count under one
          row.
        </p>
      </div>
    </div>
  );
}

// --- Run History Table ---
function RunHistory({ runs }: { runs: PipelineHistoryRun[] }) {
  if (runs.length === 0) return <p className="text-ink-min text-xs">No pipeline runs recorded.</p>;
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
    </div>
  );
}

/**
 * One "PIPELINE  STATUS · detail" row plus its stuck-run clear button.
 * Shared by the House and Stock Trades rows (which both track a
 * possibly-stuck DB run separate from the in-memory running flag); Senate
 * has no stuck-run concept and renders its own row inline.
 */
function PipelineStatusRow({
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
function PipelineRunDetailCard({
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
    <div className="panel mb-6">
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

function LastRunSteps({ steps }: { steps?: PipelineStepInfo[] | null }) {
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

// --- Data Inventory ---
const INVENTORY_SECTIONS: { label: string; keys: { key: string; label: string }[] }[] = [
  {
    label: "SENATE",
    keys: [
      { key: "senators", label: "SENATORS" },
      { key: "senatorDonors", label: "DONORS" },
      { key: "senatorIndustryDonations", label: "INDUSTRY $" },
      { key: "senatorVotes", label: "VOTES" },
      { key: "senatorLobbyingMatches", label: "LOBBY MATCHES" },
      { key: "senatorPromises", label: "PROMISES" },
      { key: "senatorBills", label: "BILLS" },
    ],
  },
  {
    label: "HOUSE",
    keys: [
      { key: "representatives", label: "REPS" },
      { key: "repDonors", label: "DONORS" },
      { key: "repIndustryDonations", label: "INDUSTRY $" },
      { key: "repVotes", label: "VOTES" },
      { key: "repLobbyingMatches", label: "LOBBY MATCHES" },
      { key: "repPromises", label: "PROMISES" },
      { key: "repBills", label: "BILLS" },
    ],
  },
  {
    label: "EXECUTIVE & JUDICIARY",
    keys: [
      { key: "presidents", label: "PRESIDENTS" },
      { key: "justices", label: "JUSTICES" },
      { key: "justiceVotes", label: "JUSTICE VOTES" },
    ],
  },
  {
    label: "ACTION CENTER",
    keys: [
      { key: "actionIssues", label: "ISSUES" },
      { key: "nationalMonitors", label: "MONITORS" },
      { key: "monitorUpdates", label: "MONITOR UPDATES" },
      { key: "timelineEntries", label: "TIMELINE ENTRIES" },
      { key: "exploreDocuments", label: "EXPLORE DOCS" },
    ],
  },
  {
    label: "SYSTEM",
    keys: [
      { key: "scoreSnapshots", label: "SCORE SNAPSHOTS" },
      { key: "learnedClassifications", label: "LEARNED CLASSES" },
      { key: "pipelineRuns", label: "PIPELINE RUNS" },
      { key: "apiCacheEntries", label: "API CACHE" },
      { key: "analysisCacheEntries", label: "ANALYSIS CACHE" },
    ],
  },
];

function DataInventory({ data }: { data: Record<string, number> }) {
  const total = Object.values(data).reduce((s, n) => s + n, 0);

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-xs text-ink-min tracking-widest">TOTAL RECORDS</span>
        <span className="font-mono text-sm text-ink-hi">{total.toLocaleString()}</span>
      </div>
      {INVENTORY_SECTIONS.map((section) => {
        const sectionTotal = section.keys.reduce((s, { key }) => s + (data[key] ?? 0), 0);
        if (sectionTotal === 0 && section.label !== "SYSTEM") return null;
        return (
          <div key={section.label}>
            <div className="flex items-center gap-2 mb-2">
              <span className="font-mono text-xs text-ind-purple tracking-widest">
                {section.label}
              </span>
              <span className="text-xs font-mono text-ink-min">
                {sectionTotal.toLocaleString()}
              </span>
              <div className="flex-1 border-t border-white/[0.07]" />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
              {section.keys.map(({ key, label }) => (
                <div key={key} className="border border-white/[0.07] p-2.5 text-center">
                  <div className="text-base font-mono text-ink-hi">
                    {(data[key] ?? 0).toLocaleString()}
                  </div>
                  <div className="text-xs font-mono text-ink-lo tracking-wider mt-0.5">{label}</div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

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

// --- Main Admin Dashboard ---
function AdminDashboardView({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<AdminPipelineStatus | null>(null);
  const [history, setHistory] = useState<PipelineHistoryRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [completionBanner, setCompletionBanner] = useState<{
    status: "completed" | "failed";
    duration: string;
  } | null>(null);
  const [clearingHouse, setClearingHouse] = useState(false);
  const [clearingStockTrades, setClearingStockTrades] = useState(false);
  const [clearingSupplementary, setClearingSupplementary] = useState(false);

  const wasRunningRef = useRef(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Deliberately not `async`: every state write lands in a `.then`/`.finally`
  // callback, so nothing here can run synchronously inside the effect that
  // kicks off the first load. The three requests are one unit — a dashboard
  // showing fresh counts next to stale history is worse than showing neither.
  const loadDashboard = useCallback(() => {
    Promise.all([
      fetchAdminDashboard(token),
      fetchAdminPipelineHistory(token),
      fetchAdminPipelineStatus(token),
    ])
      .then(([d, h, s]) => {
        setDashboard(d);
        setHistory(h);
        setPipelineStatus(s);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.message === "Unauthorized") {
          onLogout();
        }
      })
      .finally(() => setLoading(false));
  }, [token, onLogout]);

  const pollStatus = useCallback(async () => {
    try {
      const s = await fetchAdminPipelineStatus(token);
      setPipelineStatus(s);

      if (!s.isRunning && wasRunningRef.current) {
        const lastStatus = s.lastRun?.status ?? "completed";
        setCompletionBanner({
          status: lastStatus === "failed" ? "failed" : "completed",
          duration: formatDuration(s.lastRun?.elapsedSeconds),
        });
        setTimeout(() => setCompletionBanner(null), 15000);
        loadDashboard();
      }
      wasRunningRef.current = s.isRunning;
    } catch {}
  }, [token, loadDashboard]);

  const dashboardPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    const anyRunning =
      (pipelineStatus?.isRunning ?? false) || (pipelineStatus?.actionRefresh?.isRunning ?? false);
    const interval = anyRunning ? 3000 : 10000;

    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(pollStatus, interval);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [pollStatus, pipelineStatus?.isRunning, pipelineStatus?.actionRefresh?.isRunning]);

  useEffect(() => {
    const isRunning = pipelineStatus?.isRunning ?? false;
    const interval = isRunning ? 10000 : 30000;

    if (dashboardPollRef.current) clearInterval(dashboardPollRef.current);
    dashboardPollRef.current = setInterval(loadDashboard, interval);
    return () => {
      if (dashboardPollRef.current) clearInterval(dashboardPollRef.current);
    };
  }, [loadDashboard, pipelineStatus?.isRunning]);

  if (loading) {
    return (
      <div className="min-h-screen bg-surface-base flex items-center justify-center">
        <span className="text-ink-hi font-mono animate-pulse">Loading dashboard...</span>
      </div>
    );
  }

  const d = dashboard;

  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="min-h-screen bg-surface-base text-ink-hi px-4 py-8"
    >
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="font-mono text-sm sm:text-lg tracking-widest">CIVITAS // ADMIN</h1>
          <button
            onClick={onLogout}
            aria-label="Log out of admin"
            className="text-xs font-mono text-ink-lo hover:text-signal-magenta border border-signal-magenta/40 hover:border-signal-magenta/40
                       px-3 py-1  transition-colors"
          >
            [LOGOUT]
          </button>
        </div>

        {/* Completion banner */}
        {completionBanner && (
          <div
            role="status"
            aria-live="polite"
            className={`mb-6 border  p-4 flex items-center justify-between ${
              completionBanner.status === "completed"
                ? "border-phos/40 bg-white/[0.03]"
                : "border-signal-magenta/40 bg-signal-magenta/10"
            }`}
          >
            <span
              className={`text-sm font-mono font-bold ${
                completionBanner.status === "completed" ? "text-ink-hi" : "text-signal-magenta"
              }`}
            >
              {completionBanner.status === "completed"
                ? "PIPELINE COMPLETED SUCCESSFULLY"
                : "PIPELINE FAILED"}
            </span>
            <span className="text-ink-lo text-xs font-mono">{completionBanner.duration}</span>
            <button
              onClick={() => setCompletionBanner(null)}
              className="text-ink-min hover:text-phos text-xs ml-4"
              aria-label="Dismiss"
            >
              [x]
            </button>
          </div>
        )}

        {/* Live pipeline progress — one card per pipeline type. Previously
            only ever checked pipelineStatus.isRunning (Senate), so House /
            Stock Trades / Supplementary never showed this view while
            actively running, only Senate did. */}
        {pipelineStatus?.isRunning && pipelineStatus.lastRun && (
          <div className="mb-6">
            <PipelineProgressBar
              title="SENATE PIPELINE ACTIVE"
              isRunning={pipelineStatus.isRunning}
              run={pipelineStatus.lastRun}
              etaConfig={{
                processed: pipelineStatus.lastRun.senatorsProcessed,
                total: pipelineStatus.lastRun.senatorsTotal,
                unitLabel: "senator",
              }}
              statsRow={
                <>
                  <span>LLM: {pipelineStatus.lastRun.llmCalls}</span>
                  <span>
                    Cache: {pipelineStatus.lastRun.cacheHits}H /{" "}
                    {pipelineStatus.lastRun.cacheMisses}M
                  </span>
                  <span>Bills: {pipelineStatus.lastRun.billsClassified}</span>
                  <span>
                    Senators: {pipelineStatus.lastRun.senatorsProcessed}/
                    {pipelineStatus.lastRun.senatorsTotal}
                    {pipelineStatus.lastRun.senatorsFailed > 0 && (
                      <span className="text-signal-magenta ml-1">
                        ({pipelineStatus.lastRun.senatorsFailed}F)
                      </span>
                    )}
                  </span>
                </>
              }
            />
          </div>
        )}

        {pipelineStatus?.houseIsRunning && pipelineStatus.houseLastRun && (
          <div className="mb-6">
            <PipelineProgressBar
              title="HOUSE PIPELINE ACTIVE"
              isRunning={pipelineStatus.houseIsRunning}
              run={pipelineStatus.houseLastRun}
              etaConfig={{
                processed: pipelineStatus.houseLastRun.repsProcessed,
                total: pipelineStatus.houseLastRun.repsTotal,
                unitLabel: "rep",
              }}
              statsRow={
                <span>
                  Reps: {pipelineStatus.houseLastRun.repsProcessed}/
                  {pipelineStatus.houseLastRun.repsTotal}
                  {pipelineStatus.houseLastRun.repsFailed > 0 && (
                    <span className="text-signal-magenta ml-1">
                      ({pipelineStatus.houseLastRun.repsFailed}F)
                    </span>
                  )}
                </span>
              }
            />
          </div>
        )}

        {pipelineStatus?.stockTradesIsRunning && pipelineStatus.stockTradesLastRun && (
          <div className="mb-6">
            <PipelineProgressBar
              title="STOCK TRADES PIPELINE ACTIVE"
              isRunning={pipelineStatus.stockTradesIsRunning}
              run={pipelineStatus.stockTradesLastRun}
              showPhaseBreadcrumb={false}
              statsRow={
                <span>
                  Trades: {pipelineStatus.stockTradesLastRun.houseTradesIngested}H /{" "}
                  {pipelineStatus.stockTradesLastRun.senateTradesIngested}S /{" "}
                  {pipelineStatus.stockTradesLastRun.presidentTradesIngested}P
                </span>
              }
            />
          </div>
        )}

        {pipelineStatus?.supplementaryIsRunning && pipelineStatus.supplementaryLastRun && (
          <div className="mb-6">
            <PipelineProgressBar
              title="SUPPLEMENTARY PIPELINE ACTIVE"
              isRunning={pipelineStatus.supplementaryIsRunning}
              run={pipelineStatus.supplementaryLastRun}
              showPhaseBreadcrumb={false}
              statsRow={
                <>
                  <span>Docs: {pipelineStatus.supplementaryLastRun.exploreDocsIngested}</span>
                  <span>
                    SCOTUS:{" "}
                    {pipelineStatus.supplementaryLastRun.justicesSkipped
                      ? "skipped"
                      : pipelineStatus.supplementaryLastRun.justicesScored}
                  </span>
                  <span>Presidents: {pipelineStatus.supplementaryLastRun.presidentsUpdated}</span>
                </>
              }
            />
          </div>
        )}

        {pipelineStatus?.electionIsRunning && pipelineStatus.electionLastRun && (
          <div className="mb-6">
            <PipelineProgressBar
              title="ELECTION PIPELINE ACTIVE"
              isRunning={pipelineStatus.electionIsRunning}
              run={pipelineStatus.electionLastRun}
              showPhaseBreadcrumb={false}
              statsRow={
                <>
                  <span>Candidates: {pipelineStatus.electionLastRun.candidatesSynced}</span>
                  <span>Financials: {pipelineStatus.electionLastRun.financialsRefreshed}</span>
                  <span>Coverage: {pipelineStatus.electionLastRun.coverageItemsIngested}</span>
                </>
              }
            />
          </div>
        )}

        {/* Uptime Tracker */}
        <UptimeTracker uptime={d?.uptime} hostUptime={d?.host?.uptimeSeconds} />

        {/* System Monitor */}
        <SystemMonitor token={token} initialStats={d?.host} />

        {/* Seat Vacancy Control */}
        <VacancyControl token={token} />

        {/* Visitor Stats */}
        <VisitorStats token={token} />

        {/* Pipeline Phase Timings */}
        <PipelinePhaseTimings token={token} />

        {/* System Health */}
        <div className="grid grid-cols-1 mb-6">
          {/* System Health */}
          <div className="panel">
            <TerminalTitlebar title="System health" />
            <div className="p-4 space-y-2 text-sm font-mono">
              <div className="flex justify-between">
                <span className="text-ink-lo">DATABASE</span>
                <span className="flex items-center gap-2">
                  <StatusDot ok={d?.system.database === "ok"} />
                  {d?.system.database?.toUpperCase()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-lo">OLLAMA</span>
                <span className="flex items-center gap-2">
                  <StatusDot ok={d?.system.ollama === "ok"} />
                  {d?.system.ollama?.toUpperCase()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-lo">MODEL</span>
                <span className="text-signal-cyan">{d?.system.ollamaModel}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-lo">DB SIZE</span>
                <span>{formatBytes(d?.system.dbSizeBytes ?? 0)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink-lo">VECTOR DB</span>
                <span className="flex items-center gap-2">
                  <StatusDot ok={d?.system.vectorDb?.status === "ok"} />
                  {d?.system.vectorDb?.status === "ok"
                    ? `${(d?.system.vectorDb?.totalVectors ?? 0).toLocaleString()} vectors / ${formatBytes(d?.system.vectorDb?.sizeBytes ?? 0)}`
                    : "UNAVAILABLE"}
                </span>
              </div>
              <div className="border-t border-white/[0.07] pt-2 mt-2 space-y-1.5">
                {/* Senate pipeline */}
                <div className="flex justify-between">
                  <span className="text-ink-lo">SENATE</span>
                  <span
                    className={
                      pipelineStatus?.isRunning ? "text-signal-cyan animate-pulse" : "text-ink-lo"
                    }
                  >
                    {pipelineStatus?.isRunning
                      ? "RUNNING"
                      : (() => {
                          const run = pipelineStatus?.lastRun;
                          if (!run) return "IDLE";
                          const senators = `${run.senatorsProcessed}/${run.senatorsTotal}`;
                          const failed = run.senatorsFailed > 0 ? ` · ${run.senatorsFailed}F` : "";
                          return (
                            <span>
                              <span
                                className={
                                  run.status === "completed"
                                    ? "text-ink-hi"
                                    : run.status === "failed"
                                      ? "text-signal-magenta"
                                      : "text-ink-lo"
                                }
                              >
                                {run.status.toUpperCase()}
                              </span>
                              <span className="text-ink-lo"> · {senators}</span>
                              {run.senatorsFailed > 0 && (
                                <span className="text-signal-magenta">{failed}</span>
                              )}
                            </span>
                          );
                        })()}
                  </span>
                </div>
                <LastRunSteps steps={pipelineStatus?.lastRun?.progressSteps} />

                {/* House pipeline */}
                {(() => {
                  const run = pipelineStatus?.houseLastRun;
                  const isStuck = run?.status === "running" && !pipelineStatus?.houseIsRunning;
                  const statusClassName =
                    run?.status === "completed"
                      ? "text-ink-hi"
                      : run?.status === "partial"
                        ? "text-signal-amber"
                        : run?.status === "failed"
                          ? "text-signal-magenta"
                          : isStuck
                            ? "text-signal-amber"
                            : "text-ink-lo";
                  return (
                    <>
                      <PipelineStatusRow
                        label="HOUSE"
                        isRunning={!!pipelineStatus?.houseIsRunning}
                        run={run}
                        isStuck={isStuck}
                        statusClassName={statusClassName}
                        detail={
                          run && (
                            <>
                              {run.repsTotal > 0 && (
                                <span className="text-ink-lo">
                                  {" "}
                                  · {run.repsProcessed}/{run.repsTotal}
                                </span>
                              )}
                              {(run.repsFailed ?? 0) > 0 && (
                                <span className="text-signal-magenta"> · {run.repsFailed}F</span>
                              )}
                            </>
                          )
                        }
                        clearing={clearingHouse}
                        onClear={async () => {
                          setClearingHouse(true);
                          try {
                            await clearStuckHousePipeline(token);
                            await pollStatus();
                          } catch {}
                          setClearingHouse(false);
                        }}
                      />
                      <LastRunSteps steps={run?.progressSteps} />
                    </>
                  );
                })()}

                {/* Stock trades pipeline */}
                {(() => {
                  const run = pipelineStatus?.stockTradesLastRun;
                  const isStuck =
                    run?.status === "running" && !pipelineStatus?.stockTradesIsRunning;
                  const statusClassName =
                    run?.status === "completed"
                      ? "text-ink-hi"
                      : run?.status === "failed"
                        ? "text-signal-magenta"
                        : isStuck
                          ? "text-signal-amber"
                          : "text-ink-lo";
                  return (
                    <>
                      <PipelineStatusRow
                        label="STOCK TRADES"
                        isRunning={!!pipelineStatus?.stockTradesIsRunning}
                        run={run}
                        isStuck={isStuck}
                        statusClassName={statusClassName}
                        detail={
                          run && (
                            <span className="text-ink-lo">
                              {" "}
                              · {run.houseTradesIngested}H/{run.senateTradesIngested}S/
                              {run.presidentTradesIngested}P
                            </span>
                          )
                        }
                        clearing={clearingStockTrades}
                        onClear={async () => {
                          setClearingStockTrades(true);
                          try {
                            await clearStuckStockTradesPipeline(token);
                            await pollStatus();
                          } catch {}
                          setClearingStockTrades(false);
                        }}
                      />
                      <LastRunSteps steps={run?.progressSteps} />
                    </>
                  );
                })()}

                {/* Supplementary pipeline: explore docs + SCOTUS + presidents.
                    Independent of Senate (see supplementary_pipeline.py) —
                    was previously nested inside Senate's own progress steps
                    despite having no data dependency on it. */}
                {(() => {
                  const run = pipelineStatus?.supplementaryLastRun;
                  const isStuck =
                    run?.status === "running" && !pipelineStatus?.supplementaryIsRunning;
                  const statusClassName =
                    run?.status === "completed"
                      ? "text-ink-hi"
                      : run?.status === "failed"
                        ? "text-signal-magenta"
                        : isStuck
                          ? "text-signal-amber"
                          : "text-ink-lo";
                  return (
                    <>
                      <PipelineStatusRow
                        label="SUPPLEMENTARY"
                        isRunning={!!pipelineStatus?.supplementaryIsRunning}
                        run={run}
                        isStuck={isStuck}
                        statusClassName={statusClassName}
                        detail={
                          run && (
                            <span className="text-ink-lo">
                              {" "}
                              · {run.exploreDocsIngested} docs ·{" "}
                              {run.justicesSkipped
                                ? "SCOTUS skipped"
                                : `${run.justicesScored} justices`}{" "}
                              · {run.presidentsUpdated} pres
                            </span>
                          )
                        }
                        clearing={clearingSupplementary}
                        onClear={async () => {
                          setClearingSupplementary(true);
                          try {
                            await clearStuckSupplementaryPipeline(token);
                            await pollStatus();
                          } catch {}
                          setClearingSupplementary(false);
                        }}
                      />
                      <LastRunSteps steps={run?.progressSteps} />
                    </>
                  );
                })()}

                <div className="flex justify-between">
                  <span className="text-ink-lo">SCHEDULE</span>
                  <span className="text-ink">{d?.pipeline.cronSchedule}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-ink-lo">NEXT RUN</span>
                  <span className="text-ink">{formatTime(d?.pipeline.nextScheduled)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Action Center */}
        <div className="panel mb-6">
          <TerminalTitlebar title="Action center" />
          <ActionCenterStatus ac={pipelineStatus?.actionRefresh ?? null} />
        </div>

        {/* Data Stats */}
        <div className="panel mb-6">
          <TerminalTitlebar title="Data inventory" />
          {d && <DataInventory data={d.data} />}
        </div>

        {/* Vector DB & ML Metrics */}
        {d?.system.vectorDb && (
          <div className="panel mb-6">
            <TerminalTitlebar title="Vector index" />
            <div className="p-4 space-y-4">
              {d.system.vectorDb.status !== "ok" ? (
                <p className="text-signal-magenta text-xs font-mono">
                  VECTOR DB UNAVAILABLE: {d.system.vectorDb.error}
                </p>
              ) : (
                <>
                  {/* Embedding Model */}
                  <div>
                    <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                      EMBEDDING MODELS
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="border border-white/[0.07] p-3">
                        <div className="text-xs font-mono text-ink-min mb-1">
                          CLASSIFICATION MODEL
                        </div>
                        <div className="text-xs font-mono text-signal-cyan break-all">
                          {d.system.vectorDb.embeddingModel}
                        </div>
                        <div className="text-xs font-mono text-ink-lo mt-1">
                          v: {d.system.vectorDb.embeddingModelVersion}
                        </div>
                      </div>
                      <div className="border border-white/[0.07] p-3">
                        <div className="text-xs font-mono text-ink-min mb-1">
                          SEARCH INDEX MODEL
                        </div>
                        <div className="text-sm font-mono text-ink-hi">
                          {d.system.vectorDb.indexModelVersion || "rebuilding…"}
                        </div>
                      </div>
                      <div className="border border-white/[0.07] p-3">
                        <div className="text-xs font-mono text-ink-min mb-1">DIMENSIONS</div>
                        <div className="text-sm font-mono text-ink-hi">
                          {d.system.vectorDb.embeddingDimensions}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Collections */}
                  <div>
                    <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                      COLLECTIONS ({d.system.vectorDb.collections?.length ?? 0})
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {d.system.vectorDb.collections?.map((col) => {
                        const pct = d.system.vectorDb!.totalVectors
                          ? Math.round((col.count / d.system.vectorDb!.totalVectors!) * 100)
                          : 0;
                        return (
                          <div key={col.name} className="border border-white/[0.07] p-3">
                            <div className="flex justify-between items-center mb-2">
                              <span className="text-xs font-mono text-ink-hi">{col.name}</span>
                              <span className="text-xs font-mono text-signal-cyan">
                                {col.count.toLocaleString()}
                              </span>
                            </div>
                            <div
                              className="w-full h-1.5 bg-white/[0.03] overflow-hidden mb-2"
                              role="progressbar"
                              aria-valuenow={pct}
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-label={`${col.name} vector count`}
                            >
                              <div
                                className="h-full bg-phos transition-all"
                                style={{ width: `${Math.max(pct, 2)}%` }}
                              />
                            </div>
                            <div className="text-xs font-mono text-ink-min">
                              {pct}% of total vectors
                            </div>
                            {col.sampleMetadataKeys && col.sampleMetadataKeys.length > 0 && (
                              <div className="mt-1 text-xs font-mono text-ink-min">
                                fields: {col.sampleMetadataKeys.join(", ")}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Storage Summary */}
                  <div>
                    <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">STORAGE</h3>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {(d.system.vectorDb.totalVectors ?? 0).toLocaleString()}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">TOTAL VECTORS</div>
                      </div>
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {formatBytes(d.system.vectorDb.sizeBytes ?? 0)}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">DISK SIZE</div>
                      </div>
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {d.system.vectorDb.collections?.length ?? 0}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">COLLECTIONS</div>
                      </div>
                      <div className="border border-white/[0.07] p-3 text-center">
                        <div className="text-lg font-mono text-ink-hi">
                          {d.system.vectorDb.totalVectors && d.system.vectorDb.sizeBytes
                            ? `${Math.round(d.system.vectorDb.sizeBytes / d.system.vectorDb.totalVectors)} B`
                            : "—"}
                        </div>
                        <div className="text-xs font-mono text-ink-lo">AVG PER VECTOR</div>
                      </div>
                    </div>
                  </div>

                  {/* Learning Store */}
                  {d.system.vectorDb.learningStore && !d.system.vectorDb.learningStore.error && (
                    <div>
                      <h3 className="text-xs font-mono text-ink-lo tracking-wider mb-2">
                        LEARNING STORE
                      </h3>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                        <div className="border border-white/[0.07] p-3 text-center">
                          <div className="text-lg font-mono text-ink-hi">
                            {d.system.vectorDb.learningStore.totalEntries.toLocaleString()}
                          </div>
                          <div className="text-xs font-mono text-ink-lo">CLASSIFICATIONS</div>
                        </div>
                        <div className="border border-white/[0.07] p-3 text-center">
                          <div className="text-lg font-mono text-ink-hi">
                            {d.system.vectorDb.learningStore.avgConfidence != null
                              ? `${(d.system.vectorDb.learningStore.avgConfidence * 100).toFixed(1)}%`
                              : "—"}
                          </div>
                          <div className="text-xs font-mono text-ink-lo">AVG CONFIDENCE</div>
                        </div>
                        <div className="border border-white/[0.07] p-3 text-center">
                          <div className="text-lg font-mono text-ink-hi">
                            {Object.keys(d.system.vectorDb.learningStore.bySource).length}
                          </div>
                          <div className="text-xs font-mono text-ink-lo">SOURCES</div>
                        </div>
                        <div className="border border-white/[0.07] p-3 text-center">
                          <div className="text-lg font-mono text-ink-hi">
                            {Object.keys(d.system.vectorDb.learningStore.byType).length}
                          </div>
                          <div className="text-xs font-mono text-ink-lo">ENTITY TYPES</div>
                        </div>
                      </div>

                      {/* By Source breakdown */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="border border-white/[0.07] p-3">
                          <div className="text-xs font-mono text-ink-min mb-2">BY SOURCE</div>
                          <div className="space-y-1.5">
                            {Object.entries(d.system.vectorDb.learningStore.bySource)
                              .sort(([, a], [, b]) => (b as number) - (a as number))
                              .map(([source, count]) => {
                                const total = d.system.vectorDb!.learningStore!.totalEntries;
                                const pct = total
                                  ? Math.round(((count as number) / total) * 100)
                                  : 0;
                                return (
                                  <div key={source}>
                                    <div className="flex justify-between text-xs font-mono mb-0.5">
                                      <span className="text-ink">{source}</span>
                                      <span className="text-ink-lo">
                                        {(count as number).toLocaleString()} ({pct}%)
                                      </span>
                                    </div>
                                    <div
                                      className="w-full h-1 bg-white/[0.03] overflow-hidden"
                                      role="progressbar"
                                      aria-valuenow={pct}
                                      aria-valuemin={0}
                                      aria-valuemax={100}
                                      aria-label={`${source} classifications`}
                                    >
                                      <div
                                        className="h-full bg-signal-cyan"
                                        style={{ width: `${Math.max(pct, 1)}%` }}
                                      />
                                    </div>
                                  </div>
                                );
                              })}
                          </div>
                        </div>
                        <div className="border border-white/[0.07] p-3">
                          <div className="text-xs font-mono text-ink-min mb-2">BY ENTITY TYPE</div>
                          <div className="space-y-1.5">
                            {Object.entries(d.system.vectorDb.learningStore.byType)
                              .sort(([, a], [, b]) => (b as number) - (a as number))
                              .map(([type, count]) => {
                                const total = d.system.vectorDb!.learningStore!.totalEntries;
                                const pct = total
                                  ? Math.round(((count as number) / total) * 100)
                                  : 0;
                                return (
                                  <div key={type}>
                                    <div className="flex justify-between text-xs font-mono mb-0.5">
                                      <span className="text-ink">{type}</span>
                                      <span className="text-ink-lo">
                                        {(count as number).toLocaleString()} ({pct}%)
                                      </span>
                                    </div>
                                    <div
                                      className="w-full h-1 bg-white/[0.03] overflow-hidden"
                                      role="progressbar"
                                      aria-valuenow={pct}
                                      aria-valuemin={0}
                                      aria-valuemax={100}
                                      aria-label={`${type} classifications`}
                                    >
                                      <div
                                        className="h-full bg-phos"
                                        style={{ width: `${Math.max(pct, 1)}%` }}
                                      />
                                    </div>
                                  </div>
                                );
                              })}
                          </div>
                        </div>
                      </div>

                      {/* Confidence Distribution */}
                      {Object.keys(d.system.vectorDb.learningStore.confidenceDistribution).length >
                        0 && (
                        <div className="border border-white/[0.07] p-3 mt-3">
                          <div className="text-xs font-mono text-ink-min mb-2">
                            CONFIDENCE DISTRIBUTION
                          </div>
                          <div className="flex items-end gap-1 h-16">
                            {(() => {
                              const dist = d.system.vectorDb!.learningStore!.confidenceDistribution;
                              const buckets = Array.from({ length: 11 }, (_, i) =>
                                (i / 10).toFixed(1)
                              );
                              const maxCount = Math.max(
                                ...buckets.map((b) => (dist[b] ?? 0) as number),
                                1
                              );
                              return buckets.map((bucket) => {
                                const count = (dist[bucket] ?? 0) as number;
                                const height =
                                  count > 0 ? Math.max((count / maxCount) * 100, 5) : 0;
                                return (
                                  <div
                                    key={bucket}
                                    className="flex-1 flex flex-col items-center gap-0.5"
                                    title={`${bucket}: ${count} entries`}
                                  >
                                    <div
                                      className="w-full flex items-end justify-center"
                                      style={{ height: "48px" }}
                                    >
                                      <div
                                        className="w-full min-w-[4px] bg-signal-cyan transition-all"
                                        style={{ height: `${height}%` }}
                                      />
                                    </div>
                                    <span className="text-xs font-mono text-ink-min">{bucket}</span>
                                  </div>
                                );
                              });
                            })()}
                          </div>
                        </div>
                      )}

                      {/* Timestamps */}
                      <div className="flex gap-4 mt-2 text-xs font-mono text-ink-min">
                        {d.system.vectorDb.learningStore.oldestEntry && (
                          <span>
                            oldest: {formatTime(d.system.vectorDb.learningStore.oldestEntry)}
                          </span>
                        )}
                        {d.system.vectorDb.learningStore.newestEntry && (
                          <span>
                            newest: {formatTime(d.system.vectorDb.learningStore.newestEntry)}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        {/* Last Run Details */}
        {d?.pipeline.lastRun && (
          <div className="panel mb-6">
            <TerminalTitlebar title="Last run" />
            <div className="p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-mono">
                <div>
                  <span className="text-ink-lo text-xs block">STATUS</span>
                  <span
                    className={
                      d.pipeline.lastRun.status === "completed"
                        ? "text-ink-hi"
                        : d.pipeline.lastRun.status === "failed"
                          ? "text-signal-magenta"
                          : "text-signal-cyan"
                    }
                  >
                    {d.pipeline.lastRun.status.toUpperCase()}
                  </span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">STARTED</span>
                  <span>{formatTime(d.pipeline.lastRun.startedAt)}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">DURATION</span>
                  <span>{formatDuration(d.pipeline.lastRun.elapsedSeconds)}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">SENATORS</span>
                  <span>
                    {d.pipeline.lastRun.senatorsProcessed}/{d.pipeline.lastRun.senatorsTotal}
                    {d.pipeline.lastRun.senatorsFailed > 0 && (
                      <span className="text-signal-magenta ml-1">
                        ({d.pipeline.lastRun.senatorsFailed} failed)
                      </span>
                    )}
                  </span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">LLM CALLS</span>
                  <span>{d.pipeline.lastRun.llmCalls}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">BILLS CLASSIFIED</span>
                  <span>{d.pipeline.lastRun.billsClassified}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">CACHE HIT RATE</span>
                  <span>
                    {d.pipeline.lastRun.cacheHits + d.pipeline.lastRun.cacheMisses > 0
                      ? `${Math.round(
                          (d.pipeline.lastRun.cacheHits /
                            (d.pipeline.lastRun.cacheHits + d.pipeline.lastRun.cacheMisses)) *
                            100
                        )}%`
                      : "—"}{" "}
                    <span className="text-ink-min">
                      ({d.pipeline.lastRun.cacheHits}H / {d.pipeline.lastRun.cacheMisses}M)
                    </span>
                  </span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">PIPELINE RUNS</span>
                  <span>
                    {d.pipeline.totalRuns} total
                    <span className="text-ink-min">
                      {" "}
                      ({d.pipeline.successfulRuns}✓ {d.pipeline.failedRuns}✗)
                    </span>
                  </span>
                </div>
              </div>
              {d.pipeline.lastRun.errorMessage && (
                <div className="mt-3 p-2 border border-signal-magenta/40 bg-signal-magenta/10">
                  <span className="text-signal-magenta text-xs font-mono">
                    ERROR: {d.pipeline.lastRun.errorMessage}
                  </span>
                </div>
              )}
              <LastRunSteps steps={d.pipeline.lastRun.progressSteps} />
            </div>
          </div>
        )}

        <PipelineRunDetailCard
          title="last_run_detail_house"
          run={pipelineStatus?.houseLastRun}
          extraStats={
            pipelineStatus?.houseLastRun && (
              <div>
                <span className="text-ink-lo text-xs block">REPS</span>
                <span>
                  {pipelineStatus.houseLastRun.repsProcessed}/
                  {pipelineStatus.houseLastRun.repsTotal}
                  {pipelineStatus.houseLastRun.repsFailed > 0 && (
                    <span className="text-signal-magenta ml-1">
                      ({pipelineStatus.houseLastRun.repsFailed} failed)
                    </span>
                  )}
                </span>
              </div>
            )
          }
        />

        <PipelineRunDetailCard
          title="last_run_detail_stock_trades"
          run={pipelineStatus?.stockTradesLastRun}
          extraStats={
            pipelineStatus?.stockTradesLastRun && (
              <div>
                <span className="text-ink-lo text-xs block">TRADES INGESTED</span>
                <span>
                  {pipelineStatus.stockTradesLastRun.houseTradesIngested}H /{" "}
                  {pipelineStatus.stockTradesLastRun.senateTradesIngested}S /{" "}
                  {pipelineStatus.stockTradesLastRun.presidentTradesIngested}P
                </span>
              </div>
            )
          }
        />

        <PipelineRunDetailCard
          title="last_run_detail_supplementary"
          run={pipelineStatus?.supplementaryLastRun}
          extraStats={
            pipelineStatus?.supplementaryLastRun && (
              <>
                <div>
                  <span className="text-ink-lo text-xs block">EXPLORE DOCS</span>
                  <span>{pipelineStatus.supplementaryLastRun.exploreDocsIngested}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">SCOTUS</span>
                  <span>
                    {pipelineStatus.supplementaryLastRun.justicesSkipped
                      ? "skipped"
                      : `${pipelineStatus.supplementaryLastRun.justicesScored} scored`}
                  </span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">PRESIDENTS</span>
                  <span>{pipelineStatus.supplementaryLastRun.presidentsUpdated}</span>
                </div>
              </>
            )
          }
        />

        <PipelineRunDetailCard
          title="last_run_detail_election"
          run={pipelineStatus?.electionLastRun}
          extraStats={
            pipelineStatus?.electionLastRun && (
              <>
                <div>
                  <span className="text-ink-lo text-xs block">CANDIDATES SYNCED</span>
                  <span>{pipelineStatus.electionLastRun.candidatesSynced}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">FINANCIALS</span>
                  <span>{pipelineStatus.electionLastRun.financialsRefreshed}</span>
                </div>
                <div>
                  <span className="text-ink-lo text-xs block">COVERAGE</span>
                  <span>{pipelineStatus.electionLastRun.coverageItemsIngested}</span>
                </div>
              </>
            )
          }
        />

        {/* LLM Stats */}
        {d?.llm && Object.keys(d.llm).length > 0 && (
          <div className="panel mb-6">
            <TerminalTitlebar title="Model stats" />
            <div className="p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-mono">
                {Object.entries(d.llm)
                  .filter(([, val]) => val === null || typeof val !== "object")
                  .map(([key, val]) => (
                    <div key={key}>
                      <span className="text-ink-lo text-xs block">
                        {key.replace(/_/g, " ").toUpperCase()}
                      </span>
                      <span>{val === null ? "—" : String(val)}</span>
                    </div>
                  ))}
              </div>
            </div>
          </div>
        )}

        {/* Pipeline History */}
        <div className="panel mb-6">
          <TerminalTitlebar title="Run history" />
          <div className="p-4">
            <RunHistory runs={history} />
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-ink-min text-xs font-mono mt-8">
          <button
            onClick={loadDashboard}
            className="text-ink-min hover:text-phos transition-colors"
          >
            [REFRESH DASHBOARD]
          </button>
        </div>
      </div>
    </main>
  );
}

// --- Root Admin Page ---
export default function AdminPage() {
  const { token, ready, signIn: handleLogin, signOut: handleLogout } = useSessionToken(TOKEN_KEY);

  if (!ready) return null;

  if (!token) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return <AdminDashboardView token={token} onLogout={handleLogout} />;
}
