"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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
  setPoliticianVacancy,
  clearStuckHousePipeline,
  clearStuckStockTradesPipeline,
  clearStuckSupplementaryPipeline,
  type AdminDashboard,
  type AdminPipelineStatus,
  type ActionRefreshState,
  type HostStats,
  type PipelineRunInfo,
  type PipelineStepInfo,
  type UptimeInfo,
  type VisitorStatsDay,
  type VisitorBreakdown,
  type TopPageEntry,
} from "@/lib/api";

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
      className={`inline-block w-2 h-2 rounded-full ${ok ? "bg-matrix-green" : "bg-neon-pink"}`}
      aria-label={ok ? "Healthy" : "Unhealthy"}
    />
  );
}

// --- Login Screen ---
function LoginScreen({
  onLogin,
}: {
  onLogin: (token: string) => void;
}) {
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
    <div className="min-h-screen bg-crt-black flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="terminal-window">
          <TerminalTitlebar title="admin_auth.sh" />
          <div className="p-6">
            <h1 className="font-pixel text-sm text-matrix-green tracking-widest mb-6 text-center">
              CIVITAS ADMIN
            </h1>
            <form onSubmit={handleSubmit}>
              <label
                htmlFor="admin-token"
                className="block text-matrix-green/60 text-xs font-terminal mb-2"
              >
                ADMIN TOKEN:
              </label>
              <input
                id="admin-token"
                type="password"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Enter admin token..."
                className="w-full bg-transparent border border-matrix-green/30 rounded px-3 py-2
                           text-matrix-green text-sm font-terminal placeholder:text-matrix-green/30
                           outline-none focus:border-matrix-green/60"
                autoFocus
                aria-invalid={!!error}
                aria-describedby={error ? "admin-token-error" : undefined}
              />
              {error && (
                <p id="admin-token-error" className="text-neon-pink text-xs mt-2" role="alert">{error}</p>
              )}
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="mt-4 w-full text-xs font-pixel text-crt-black bg-matrix-green
                           hover:bg-neon-cyan disabled:bg-matrix-green/30 disabled:text-matrix-green/50
                           transition-colors py-2 rounded"
              >
                {loading ? "AUTHENTICATING..." : "AUTHENTICATE"}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
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
          : `${m}:${String(sec).padStart(2, "0")}`,
      );
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  return <span className="tabular-nums">{elapsed || "0:00"}</span>;
}

function PhaseSteps({ currentPhase }: { currentPhase: string | null | undefined }) {
  const activeIdx = PHASE_ORDER.indexOf(
    (currentPhase ?? "fetch") as (typeof PHASE_ORDER)[number],
  );
  return (
    <div className="flex items-center gap-1 text-[10px] font-pixel tracking-wider">
      {PHASE_ORDER.map((p, i) => {
        const done = i < activeIdx;
        const active = i === activeIdx;
        return (
          <div key={p} className="flex items-center gap-1">
            {i > 0 && (
              <span
                className={`w-4 h-px ${done ? "bg-matrix-green" : "bg-matrix-green/20"}`}
              />
            )}
            <span
              className={
                active
                  ? "text-neon-cyan animate-pulse"
                  : done
                    ? "text-matrix-green"
                    : "text-matrix-green/25"
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

function useAnalyzeEta(
  isAnalyze: boolean,
  processed: number,
  total: number,
  elapsedSeconds: number | null | undefined,
  unitLabel: string = "senator",
) {
  const liveAnchorRef = useRef<{ time: number; count: number } | null>(null);
  const [eta, setEta] = useState<string | null>(null);
  const [rate, setRate] = useState<string | null>(null);

  useEffect(() => {
    if (!isAnalyze || total <= 0) {
      liveAnchorRef.current = null;
      setEta(null);
      setRate(null);
      return;
    }

    if (!liveAnchorRef.current) {
      liveAnchorRef.current = { time: Date.now(), count: processed };
    }

    const tick = () => {
      const anchor = liveAnchorRef.current;
      const remaining = total - processed;

      if (remaining <= 0) {
        setEta(null);
        setRate(null);
        return;
      }

      const liveDelta = anchor ? processed - anchor.count : 0;
      const liveElapsed = anchor ? (Date.now() - anchor.time) / 1000 : 0;

      if (liveDelta > 0 && liveElapsed > 3) {
        const secPer = liveElapsed / liveDelta;
        setRate(`${secPer.toFixed(0)}s/${unitLabel}`);
        setEta(formatEtaSeconds(Math.round(remaining * secPer)));
      } else if (processed > 0 && elapsedSeconds && elapsedSeconds > 0) {
        const secPer = elapsedSeconds / processed;
        setRate(`~${secPer.toFixed(0)}s/${unitLabel}`);
        setEta(formatEtaSeconds(Math.round(remaining * secPer)));
      } else {
        setEta(null);
        setRate(null);
      }
    };

    tick();
    const id = setInterval(tick, 2000);
    return () => clearInterval(id);
  }, [isAnalyze, processed, total, elapsedSeconds, unitLabel]);

  return { eta, rate };
}

function StepProgressMini({ step }: { step: PipelineStepInfo }) {
  if (step.total == null || step.total === 0 || step.status === "pending") return null;
  const done = step.done ?? 0;
  const pct = Math.round((done / step.total) * 100);
  return (
    <div className="mt-1">
      <div
        className="w-full h-1 bg-matrix-green/10 rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${step.label} progress`}
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            step.status === "active" ? "bg-neon-cyan/70" : "bg-matrix-green/50"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[9px] font-terminal text-matrix-green/40 tabular-nums">
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
  const { eta, rate } = useAnalyzeEta(isAnalyze, processed, total, elapsed, etaConfig?.unitLabel ?? "item");

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
    <div className="border border-neon-cyan/40 rounded p-4 bg-neon-cyan/5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-neon-cyan text-sm font-terminal font-bold flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-neon-cyan animate-pulse" />
          {title}
        </span>
        <span className="text-matrix-green/70 text-xs font-terminal">
          <ElapsedTimer startedAt={run.startedAt} />
        </span>
      </div>

      {showPhaseBreadcrumb && <PhaseSteps currentPhase={phase} />}

      {/* Overall progress bar */}
      <div className="mt-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-matrix-green/60 text-[10px] font-terminal">
            {activeStep
              ? activeStep.label.toUpperCase()
              : "INITIALIZING"}
          </span>
          <span className="text-matrix-green text-xs font-terminal tabular-nums">
            {doneSteps}/{totalSteps} steps ({overallPct}%)
          </span>
        </div>
        <div
          className="w-full h-2 bg-matrix-green/10 border border-matrix-green/20 rounded-sm overflow-hidden"
          role="progressbar"
          aria-valuenow={overallPct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Pipeline overall progress"
        >
          <div
            className="h-full bg-neon-cyan transition-all duration-700"
            style={{ width: `${overallPct}%` }}
          />
        </div>
        {isAnalyze && eta && (
          <div className="flex items-center justify-between mt-1">
            <span className="text-matrix-green/40 text-[10px] font-terminal tabular-nums">
              {rate}
            </span>
            <span className="text-neon-yellow/80 text-[10px] font-terminal tabular-nums">
              ETA: {eta}
            </span>
          </div>
        )}
      </div>

      {/* Granular sub-steps grouped by phase */}
      {steps.length > 0 && (
        <div className="mt-4 space-y-3">
          {phaseGroups.map((group) => {
            const groupDone = group.steps.every(
              (s) => s.status === "done" || s.status === "skipped",
            );
            const groupActive = group.steps.some((s) => s.status === "active");
            return (
              <div key={group.phase}>
                <div
                  className={`text-[10px] font-pixel tracking-wider mb-1.5 ${
                    groupActive
                      ? "text-neon-cyan"
                      : groupDone
                        ? "text-matrix-green/70"
                        : "text-matrix-green/30"
                  }`}
                >
                  {groupDone ? "✓ " : groupActive ? "▶ " : ""}
                  {group.label}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 pl-3 border-l border-matrix-green/15">
                  {group.steps.map((step) => (
                    <div key={step.key} className="flex items-start gap-2 min-h-[20px]">
                      <span
                        className={`mt-0.5 flex-shrink-0 w-3 text-center text-[10px] ${
                          step.status === "done"
                            ? "text-matrix-green"
                            : step.status === "active"
                              ? "text-neon-cyan animate-pulse"
                              : step.status === "skipped"
                                ? "text-matrix-green/25"
                                : "text-matrix-green/20"
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
                            className={`text-[11px] font-terminal truncate ${
                              step.status === "active"
                                ? "text-neon-cyan"
                                : step.status === "done"
                                  ? "text-matrix-green/80"
                                  : step.status === "skipped"
                                    ? "text-matrix-green/30 line-through"
                                    : "text-matrix-green/35"
                            }`}
                          >
                            {step.label}
                          </span>
                          {step.detail && (step.status === "done" || step.status === "active") && (
                            <span className="text-[9px] font-terminal text-matrix-green/40 truncate">
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
        <div className="flex gap-4 mt-3 pt-2 border-t border-matrix-green/10 text-[10px] text-matrix-green/50 font-terminal">
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
  const color =
    pct >= critAt
      ? "bg-neon-pink"
      : pct >= warnAt
        ? "bg-neon-yellow"
        : "bg-matrix-green";
  const value = Math.min(Math.round(pct), 100);
  return (
    <div
      className="w-full h-1.5 bg-matrix-green/10 rounded-sm overflow-hidden"
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
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const processStart = uptime?.processStartedAt
    ? parseUTC(uptime.processStartedAt).getTime()
    : null;
  const appUptimeSec = processStart ? Math.max(0, Math.floor((now - processStart) / 1000)) : null;

  const firstRun = uptime?.firstPipelineRun
    ? parseUTC(uptime.firstPipelineRun).getTime()
    : null;
  const totalServiceDays = firstRun
    ? Math.max(1, Math.floor((now - firstRun) / 86400000))
    : null;

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
    <div className="terminal-window mb-6">
      <TerminalTitlebar title="uptime_tracker">
        <span className="ml-auto text-white/20 text-[10px] font-terminal mr-2">
          live
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-matrix-green ml-1 animate-pulse" />
        </span>
      </TerminalTitlebar>
      <div className="p-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* App Uptime — large ticking counter */}
          <div className="sm:col-span-2">
            <div className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
              APPLICATION UPTIME
            </div>
            <div className="font-terminal text-2xl sm:text-3xl text-neon-cyan tabular-nums tracking-wider">
              {appUptimeSec != null ? tickingUptime(appUptimeSec) : "—"}
            </div>
            <div className="text-[10px] font-terminal text-matrix-green/40 mt-1.5">
              {uptime?.processStartedAt
                ? `started ${formatTime(uptime.processStartedAt)}`
                : "unknown start time"}
            </div>
          </div>

          {/* Sidebar stats */}
          <div className="space-y-3">
            <div>
              <div className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-0.5">
                HOST UPTIME
              </div>
              <div className="font-terminal text-sm text-matrix-green tabular-nums">
                {hostUptime != null ? tickingUptime(hostUptime) : "—"}
              </div>
            </div>
            <div>
              <div className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-0.5">
                SERVICE AGE
              </div>
              <div className="font-terminal text-sm text-matrix-green">
                {totalServiceDays != null
                  ? `${totalServiceDays} day${totalServiceDays !== 1 ? "s" : ""}`
                  : "—"}
              </div>
              <div className="text-[10px] font-terminal text-matrix-green/30 mt-0.5">
                {uptime?.firstPipelineRun
                  ? `since ${formatTime(uptime.firstPipelineRun)}`
                  : ""}
              </div>
            </div>
          </div>
        </div>

        {/* Uptime bar visualization */}
        {appUptimeSec != null && (
          <div className="mt-4 pt-3 border-t border-matrix-green/10">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] font-pixel text-matrix-green/40">SESSION HEALTH</span>
              <span className="text-[10px] font-terminal text-matrix-green/60 tabular-nums">
                {appUptimeSec >= 86400
                  ? `${Math.floor(appUptimeSec / 86400)}d`
                  : appUptimeSec >= 3600
                    ? `${Math.floor(appUptimeSec / 3600)}h`
                    : `${Math.floor(appUptimeSec / 60)}m`}{" "}
                since last deploy
              </span>
            </div>
            <div
              className="w-full h-2 bg-matrix-green/10 rounded-sm overflow-hidden"
              role="progressbar"
              aria-valuenow={Math.min(100, Math.round((appUptimeSec / 86400) * 100))}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Session health since last deploy"
            >
              <div
                className="h-full bg-neon-cyan/60 rounded-sm transition-all duration-1000"
                style={{
                  width: `${Math.min(100, (appUptimeSec / 86400) * 100)}%`,
                }}
              />
            </div>
            <div className="flex justify-between text-[9px] font-terminal text-matrix-green/25 mt-0.5">
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
function SystemMonitor({
  token,
  initialStats,
}: {
  token: string;
  initialStats?: HostStats;
}) {
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
        ? "text-neon-pink"
        : stats.cpuTempC >= 65
          ? "text-neon-yellow"
          : "text-matrix-green";

  const loadPct = stats.loadAvg
    ? Math.round((stats.loadAvg[0] / stats.cpuCount) * 100)
    : 0;

  return (
    <div className="terminal-window mb-6">
      <TerminalTitlebar title="system_monitor">
        <span className="ml-auto text-white/20 text-[10px] font-terminal mr-2">
          live
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-matrix-green ml-1 animate-pulse" />
        </span>
      </TerminalTitlebar>
      <div className="p-4">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          {/* CPU Load */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-matrix-green/50 text-[10px] font-pixel tracking-wider">
                CPU LOAD
              </span>
              <span className="text-matrix-green text-xs font-terminal tabular-nums">
                {stats.loadAvg
                  ? `${stats.loadAvg[0].toFixed(2)} / ${stats.cpuCount}`
                  : "—"}
              </span>
            </div>
            <UsageBar pct={loadPct} ariaLabel="CPU load percentage" />
            <div className="text-[10px] text-matrix-green/40 font-terminal mt-1 tabular-nums">
              {stats.loadAvg
                ? `${stats.loadAvg[0].toFixed(1)} · ${stats.loadAvg[1].toFixed(1)} · ${stats.loadAvg[2].toFixed(1)}`
                : ""}
            </div>
          </div>

          {/* Memory */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-matrix-green/50 text-[10px] font-pixel tracking-wider">
                MEMORY
              </span>
              <span className="text-matrix-green text-xs font-terminal tabular-nums">
                {stats.memUsedPct}%
              </span>
            </div>
            <UsageBar pct={stats.memUsedPct} ariaLabel="Memory usage percentage" />
            <div className="text-[10px] text-matrix-green/40 font-terminal mt-1 tabular-nums">
              {formatBytes(stats.memUsedBytes)} / {formatBytes(stats.memTotalBytes)}
            </div>
          </div>

          {/* Disk */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-matrix-green/50 text-[10px] font-pixel tracking-wider">
                DISK
              </span>
              <span className="text-matrix-green text-xs font-terminal tabular-nums">
                {stats.diskUsedPct}%
              </span>
            </div>
            <UsageBar pct={stats.diskUsedPct} warnAt={80} critAt={95} ariaLabel="Disk usage percentage" />
            <div className="text-[10px] text-matrix-green/40 font-terminal mt-1 tabular-nums">
              {formatBytes(stats.diskFreeBytes)} free
            </div>
          </div>

          {/* Temperature + Uptime */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-matrix-green/50 text-[10px] font-pixel tracking-wider">
                CPU TEMP
              </span>
              <span className={`text-xs font-terminal tabular-nums ${tempColor}`}>
                {stats.cpuTempC != null ? `${stats.cpuTempC}°C` : "—"}
              </span>
            </div>
            {stats.cpuTempC != null && (
              <UsageBar pct={(stats.cpuTempC / 85) * 100} warnAt={76} critAt={94} ariaLabel="CPU temperature" />
            )}
            <div className="text-[10px] text-matrix-green/40 font-terminal mt-1">
              uptime {formatUptime(stats.uptimeSeconds)}
            </div>
          </div>

          {/* Network */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-matrix-green/50 text-[10px] font-pixel tracking-wider">
                NETWORK
              </span>
              <span className="text-matrix-green text-xs font-terminal tabular-nums">
                {netRate ? formatRate(netRate.rx + netRate.tx) : "—"}
              </span>
            </div>
            <div className="space-y-1.5">
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-matrix-green/40 font-terminal">▼ RX</span>
                  <span className="text-[10px] text-matrix-green/60 font-terminal tabular-nums">
                    {netRate ? formatRate(netRate.rx) : "—"}
                  </span>
                </div>
                {netRate && (
                  <div
                    className="w-full h-1 rounded-full bg-matrix-green/10 mt-0.5"
                    role="progressbar"
                    aria-valuenow={Math.min(100, Math.round((netRate.rx / (1024 * 1024)) * 10))}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Network receive rate"
                  >
                    <div
                      className="h-full rounded-full bg-matrix-green/50 transition-all duration-500"
                      style={{ width: `${Math.min(100, (netRate.rx / (1024 * 1024)) * 10)}%` }}
                    />
                  </div>
                )}
              </div>
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-matrix-green/40 font-terminal">▲ TX</span>
                  <span className="text-[10px] text-matrix-green/60 font-terminal tabular-nums">
                    {netRate ? formatRate(netRate.tx) : "—"}
                  </span>
                </div>
                {netRate && (
                  <div
                    className="w-full h-1 rounded-full bg-matrix-green/10 mt-0.5"
                    role="progressbar"
                    aria-valuenow={Math.min(100, Math.round((netRate.tx / (1024 * 1024)) * 10))}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label="Network transmit rate"
                  >
                    <div
                      className="h-full rounded-full bg-neon-blue/50 transition-all duration-500"
                      style={{ width: `${Math.min(100, (netRate.tx / (1024 * 1024)) * 10)}%` }}
                    />
                  </div>
                )}
              </div>
            </div>
            <div className="text-[10px] text-matrix-green/40 font-terminal mt-1 tabular-nums">
              {stats.netRxBytes != null ? `↓${formatBytes(stats.netRxBytes)} ↑${formatBytes(stats.netTxBytes ?? 0)}` : ""}
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
        action === "vacate" && leftOfficeDate ? leftOfficeDate : undefined,
      );
      setResult(
        action === "vacate"
          ? `${res.name}'s seat marked vacant (${res.vacancyReason}).`
          : `${res.name}'s seat restored to current.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="terminal-window mb-6">
      <TerminalTitlebar title="seat_vacancy" />
      <div className="p-4 space-y-3">
        <p className="text-matrix-green/40 text-[10px] font-terminal">
          Marks a senator/representative&apos;s seat vacant (or restores it) without
          deleting their historical data. No automated detection — this is manual only.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <label className="text-matrix-green/50 text-[9px] font-pixel tracking-wider">
              POLITICIAN ID
            </label>
            <input
              value={politicianId}
              onChange={(e) => setPoliticianId(e.target.value)}
              placeholder="e.g. lindsey-graham"
              className="bg-terminal-bg/50 border border-matrix-green/20 text-matrix-green text-xs font-terminal px-2 py-1.5 w-48 focus:outline-none focus:border-matrix-green/50"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-matrix-green/50 text-[9px] font-pixel tracking-wider">
              ACTION
            </label>
            <select
              value={action}
              onChange={(e) => setAction(e.target.value as "vacate" | "restore")}
              className="bg-terminal-bg/50 border border-matrix-green/20 text-matrix-green text-xs font-terminal px-2 py-1.5 focus:outline-none focus:border-matrix-green/50"
            >
              <option value="vacate">Mark vacant</option>
              <option value="restore">Restore to current</option>
            </select>
          </div>
          {action === "vacate" && (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-matrix-green/50 text-[9px] font-pixel tracking-wider">
                  REASON
                </label>
                <select
                  value={reason}
                  onChange={(e) => setReason(e.target.value as (typeof VACANCY_REASONS)[number])}
                  className="bg-terminal-bg/50 border border-matrix-green/20 text-matrix-green text-xs font-terminal px-2 py-1.5 focus:outline-none focus:border-matrix-green/50"
                >
                  {VACANCY_REASONS.map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-matrix-green/50 text-[9px] font-pixel tracking-wider">
                  LEFT OFFICE
                </label>
                <input
                  type="date"
                  value={leftOfficeDate}
                  onChange={(e) => setLeftOfficeDate(e.target.value)}
                  className="bg-terminal-bg/50 border border-matrix-green/20 text-matrix-green text-xs font-terminal px-2 py-1.5 focus:outline-none focus:border-matrix-green/50"
                />
              </div>
            </>
          )}
          <button
            onClick={submit}
            disabled={submitting || !politicianId.trim()}
            className="font-pixel text-[10px] text-neon-cyan border border-neon-cyan/30 px-3 py-1.5 hover:bg-neon-cyan/10 transition-colors disabled:opacity-40"
          >
            {submitting ? "SUBMITTING..." : "SUBMIT"}
          </button>
        </div>
        {result && <p className="text-matrix-green text-xs font-terminal">{result}</p>}
        {error && <p className="text-neon-pink text-xs font-terminal">{error}</p>}
      </div>
    </div>
  );
}

// --- Visitor Stats ---
function BreakdownGroup({ title, entries }: { title: string; entries: { name: string; count: number }[] }) {
  if (entries.length === 0) return null;
  const max = Math.max(1, ...entries.map((e) => e.count));
  return (
    <div>
      <div className="text-matrix-green/40 text-[9px] font-pixel tracking-wider mb-1.5">
        {title}
      </div>
      <div className="space-y-1">
        {entries.map((e) => (
          <div key={e.name} className="flex items-center gap-2">
            <span className="text-matrix-green/50 text-[10px] font-terminal w-14 shrink-0 truncate">
              {e.name}
            </span>
            <div className="flex-1">
              <UsageBar
                pct={(e.count / max) * 100}
                warnAt={101}
                critAt={101}
                ariaLabel={`${e.count} visitors used ${e.name}`}
              />
            </div>
            <span className="text-matrix-green/60 text-[10px] font-terminal tabular-nums w-6 text-right shrink-0">
              {e.count}
            </span>
          </div>
        ))}
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
    <div className="terminal-window mb-6">
      <TerminalTitlebar title="visitor_stats" />
      <div className="p-4">
        <div className="flex items-baseline justify-between mb-4">
          <span className="text-matrix-green/50 text-[10px] font-pixel tracking-wider">
            UNIQUE VISITORS TODAY
          </span>
          <span className="text-matrix-green text-2xl font-terminal tabular-nums">
            {todayCount}
          </span>
        </div>
        {days.length === 0 ? (
          <div className="text-matrix-green/40 text-xs font-terminal">
            No visitor data yet.
          </div>
        ) : (
          <div className="space-y-1.5">
            {days.map((d) => (
              <div key={d.date} className="flex items-center gap-3">
                <span className="text-matrix-green/40 text-[10px] font-terminal tabular-nums w-16 shrink-0">
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
                <span className="text-matrix-green/60 text-[10px] font-terminal tabular-nums w-8 text-right shrink-0">
                  {d.uniqueVisitors}
                </span>
              </div>
            ))}
          </div>
        )}
        {breakdown && (breakdown.browsers.length > 0 || breakdown.os.length > 0 || breakdown.devices.length > 0) && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-4 pt-4 border-t border-matrix-green/10">
            <BreakdownGroup title="BROWSER — TODAY" entries={breakdown.browsers} />
            <BreakdownGroup title="OS — TODAY" entries={breakdown.os} />
            <BreakdownGroup title="DEVICE — TODAY" entries={breakdown.devices} />
          </div>
        )}
        {topPages.length > 0 && (
          <div className="mt-4 pt-4 border-t border-matrix-green/10">
            <div className="text-matrix-green/40 text-[9px] font-pixel tracking-wider mb-1.5">
              MOST VISITED PAGES — LAST 7 DAYS
            </div>
            <div className="space-y-1">
              {topPages.map((p) => {
                const max = Math.max(1, ...topPages.map((e) => e.views));
                return (
                  <div key={p.path} className="flex items-center gap-2">
                    <span className="text-matrix-green/50 text-[10px] font-terminal w-40 shrink-0 truncate">
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
                    <span className="text-matrix-green/60 text-[10px] font-terminal tabular-nums w-10 text-right shrink-0">
                      {p.views}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <p className="text-matrix-green/25 text-[9px] font-terminal mt-3">
          Counted by a salted, daily-rotating hash — no IP addresses are stored.
          Browser/OS/device are coarse categories only, never the raw
          User-Agent string. Page views are raw counts (not deduped by
          visitor) grouped by route, e.g. all politician profiles count under
          one row.
        </p>
      </div>
    </div>
  );
}

// --- Run History Table ---
function RunHistory({ runs }: { runs: PipelineRunInfo[] }) {
  if (runs.length === 0) return <p className="text-matrix-green/40 text-xs">No pipeline runs recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-terminal">
        <thead>
          <tr className="text-matrix-green/60 border-b border-matrix-green/20">
            <th scope="col" className="text-left py-1 pr-3">TYPE</th>
            <th scope="col" className="text-left py-1 pr-3">STARTED</th>
            <th scope="col" className="text-left py-1 pr-3">STATUS</th>
            <th scope="col" className="text-left py-1 pr-3">DURATION</th>
            <th scope="col" className="text-right py-1 pr-3">PROCESSED</th>
            <th scope="col" className="text-right py-1 pr-3">LLM</th>
            <th scope="col" className="text-right py-1">CACHE</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const isHouse = r.pipelineType === "house";
            const isStockTrades = r.pipelineType === "stock_trades";
            const isSupplementary = r.pipelineType === "supplementary";
            const isSenate = !isHouse && !isStockTrades && !isSupplementary;
            const statusColor = r.status === "completed"
              ? "text-matrix-green"
              : r.status === "partial"
                ? "text-yellow-400"
                : r.status === "failed"
                  ? "text-neon-pink"
                  : r.status === "running"
                    ? "text-neon-cyan animate-pulse"
                    : "text-matrix-green/50";
            return (
              <tr
                key={`${r.pipelineType ?? "senate"}-${r.id}`}
                className="border-b border-matrix-green/10 hover:bg-matrix-green/5"
              >
                <td className="py-1.5 pr-3">
                  <span className={isSenate ? "text-matrix-green/50" : "text-neon-cyan/70"}>
                    {isStockTrades ? "STOCK" : isHouse ? "HOUSE" : isSupplementary ? "SUPP" : "SENATE"}
                  </span>
                </td>
                <td className="py-1.5 pr-3 text-matrix-green/70">{formatTime(r.startedAt)}</td>
                <td className="py-1.5 pr-3">
                  <span className={statusColor}>
                    {r.status.toUpperCase()}
                  </span>
                  {r.errorMessage && (
                    <span className="ml-2 text-neon-pink/70 text-[10px]" title={r.errorMessage}>⚠</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-matrix-green/60">{formatDuration(r.elapsedSeconds)}</td>
                <td className="py-1.5 pr-3 text-right text-matrix-green/60">
                  {isStockTrades ? (
                    <>{r.houseTradesIngested ?? 0}H/{r.senateTradesIngested ?? 0}S</>
                  ) : isHouse ? (
                    <>
                      {r.repsProcessed ?? 0}/{r.repsTotal ?? 0}
                      {(r.repsFailed ?? 0) > 0 && (
                        <span className="text-neon-pink ml-1">({r.repsFailed}F)</span>
                      )}
                    </>
                  ) : isSupplementary ? (
                    <>{r.presidentsUpdated ?? 0}P/{r.justicesSkipped ? "—" : (r.justicesScored ?? 0)}J</>
                  ) : (
                    <>
                      {r.senatorsProcessed}/{r.senatorsTotal}
                      {r.senatorsFailed > 0 && (
                        <span className="text-neon-pink ml-1">({r.senatorsFailed}F)</span>
                      )}
                    </>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right text-matrix-green/60">
                  {isSenate ? r.llmCalls : "—"}
                </td>
                <td className="py-1.5 text-right text-matrix-green/60">
                  {isSenate && r.cacheHits + r.cacheMisses > 0
                    ? `${Math.round((r.cacheHits / (r.cacheHits + r.cacheMisses)) * 100)}%`
                    : "—"}
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
        <span className="text-matrix-green/60">{label}</span>
        <span className={isRunning ? "text-neon-cyan animate-pulse" : "text-matrix-green/60"}>
          {isRunning ? "RUNNING" : run ? (
            <span>
              <span className={statusClassName}>{isStuck ? "STUCK" : run.status.toUpperCase()}</span>
              {detail}
            </span>
          ) : "IDLE"}
        </span>
      </div>
      {isStuck && onClear && (
        <div className="flex justify-end">
          <button
            disabled={clearing}
            onClick={onClear}
            className="text-[9px] font-pixel text-yellow-400/70 hover:text-yellow-400
                       border border-yellow-400/30 hover:border-yellow-400/60
                       px-2 py-0.5 rounded transition-colors disabled:opacity-40"
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
    <div className="terminal-window mb-6">
      <TerminalTitlebar title={title} />
      <div className="p-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-terminal">
          <div>
            <span className="text-matrix-green/50 text-xs block">STATUS</span>
            <span
              className={
                run.status === "completed"
                  ? "text-matrix-green"
                  : run.status === "failed"
                    ? "text-neon-pink"
                    : "text-neon-cyan"
              }
            >
              {run.status.toUpperCase()}
            </span>
          </div>
          <div>
            <span className="text-matrix-green/50 text-xs block">STARTED</span>
            <span>{formatTime(run.startedAt)}</span>
          </div>
          <div>
            <span className="text-matrix-green/50 text-xs block">DURATION</span>
            <span>{formatDuration(run.elapsedSeconds)}</span>
          </div>
          {extraStats}
        </div>
        {run.errorMessage && (
          <div className="mt-3 p-2 border border-neon-pink/30 rounded bg-neon-pink/5">
            <span className="text-neon-pink text-xs font-terminal">
              ERROR: {run.errorMessage}
            </span>
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
    <div className="mt-4 border-t border-matrix-green/15 pt-3">
      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        aria-label={`Step breakdown, ${steps.length} steps`}
        className="text-[10px] font-pixel text-matrix-green/50 hover:text-matrix-green/80 transition-colors"
      >
        {expanded ? "▼" : "▶"} STEP BREAKDOWN ({steps.length} steps)
      </button>
      {expanded && (
        <div className="mt-2 space-y-0.5">
          {steps.map((step) => (
            <div
              key={step.key}
              className="flex items-center gap-2 text-[11px] font-terminal py-0.5"
            >
              <span
                className={`w-3 text-center flex-shrink-0 ${
                  step.status === "done"
                    ? "text-matrix-green"
                    : step.status === "skipped"
                      ? "text-matrix-green/25"
                      : "text-matrix-green/40"
                }`}
              >
                {step.status === "done" ? "✓" : step.status === "skipped" ? "—" : "○"}
              </span>
              <span
                className={`w-40 truncate ${
                  step.status === "skipped"
                    ? "text-matrix-green/30 line-through"
                    : "text-matrix-green/70"
                }`}
              >
                {step.label}
              </span>
              {step.detail && (
                <span className="text-matrix-green/40 truncate">{step.detail}</span>
              )}
              {step.total != null && step.total > 0 && (
                <span className="text-matrix-green/30 tabular-nums ml-auto">
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
        <span className="font-pixel text-[10px] text-matrix-green/40 tracking-widest">
          TOTAL RECORDS
        </span>
        <span className="font-terminal text-sm text-matrix-green">
          {total.toLocaleString()}
        </span>
      </div>
      {INVENTORY_SECTIONS.map((section) => {
        const sectionTotal = section.keys.reduce(
          (s, { key }) => s + (data[key] ?? 0),
          0,
        );
        if (sectionTotal === 0 && section.label !== "SYSTEM") return null;
        return (
          <div key={section.label}>
            <div className="flex items-center gap-2 mb-2">
              <span className="font-pixel text-[10px] text-purple-400/70 tracking-widest">
                {section.label}
              </span>
              <span className="text-[10px] font-terminal text-matrix-green/30">
                {sectionTotal.toLocaleString()}
              </span>
              <div className="flex-1 border-t border-matrix-green/10" />
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
              {section.keys.map(({ key, label }) => (
                <div
                  key={key}
                  className="border border-matrix-green/15 rounded p-2.5 text-center"
                >
                  <div className="text-base font-terminal text-matrix-green">
                    {(data[key] ?? 0).toLocaleString()}
                  </div>
                  <div className="text-[9px] font-pixel text-matrix-green/50 tracking-wider mt-0.5">
                    {label}
                  </div>
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
  fetch:    "FETCHING ARTICLES",
  filter:   "FILTERING RELEVANCE",
  cluster:  "CLUSTERING TOPICS",
  rank:     "RANKING CLUSTERS",
  issues:   "GENERATING ISSUES",
  monitors: "UPDATING MONITORS",
  theme:    "GENERATING THEME",
  stories:  "WRITING STORIES",
  bluesky:  "POSTING TO BLUESKY",
  cleanup:  "CLEANUP",
};

function useElapsedSeconds(startIso: string | null, running: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!running || !startIso) { setElapsed(0); return; }
    const update = () => {
      const diff = (Date.now() - new Date(startIso + "Z").getTime()) / 1000;
      setElapsed(Math.max(0, Math.round(diff)));
    };
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [startIso, running]);
  return elapsed;
}

function ActionCenterStatus({ ac }: { ac: ActionRefreshState | null }) {
  // Use a stable startedAt ref so the timer only resets when a genuinely new run begins
  const startedAt = ac?.isRunning ? ac.startedAt : null;
  const totalElapsed = useElapsedSeconds(startedAt, ac?.isRunning ?? false);

  if (!ac || (!ac.isRunning && !ac.lastCompletedAt)) {
    return (
      <div className="p-4 text-xs font-terminal text-matrix-green/40">
        No data yet — status available after first refresh.
      </div>
    );
  }

  const stageLabel = ac.stage ? (ACTION_STAGE_LABELS[ac.stage] ?? ac.stage.toUpperCase()) : null;

  // Parse N/M progress detail
  const progressMatch = ac.stageDetail ? /^(\d+)\/(\d+)/.exec(ac.stageDetail) : null;
  const progressDone = progressMatch ? parseInt(progressMatch[1]) : null;
  const progressTotal = progressMatch ? parseInt(progressMatch[2]) : null;
  const progressPct = progressDone !== null && progressTotal && progressTotal > 0
    ? Math.round((progressDone / progressTotal) * 100) : null;

  // Sub-step detail (text after N/M)
  const subStep = ac.stageDetail && !progressMatch ? ac.stageDetail : null;

  return (
    <div className="p-4 space-y-3 text-xs font-terminal">
      {/* Status + elapsed */}
      <div className="flex items-center justify-between">
        <span className="text-matrix-green/60">STATUS</span>
        <span className={ac.isRunning ? "text-neon-cyan animate-pulse font-bold" : "text-matrix-green/60"}>
          {ac.isRunning
            ? `RUNNING · ${formatDuration(totalElapsed)}`
            : "IDLE"}
        </span>
      </div>

      {/* Live stage — shown when running */}
      {ac.isRunning && stageLabel && (
        <div className="border border-neon-cyan/20 rounded px-3 py-2 bg-neon-cyan/5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-neon-cyan font-bold tracking-wider">{stageLabel}</span>
            <span className="text-neon-cyan/70 shrink-0">
              {progressDone !== null && progressTotal !== null
                ? `${progressDone}/${progressTotal}`
                : subStep ?? ""}
            </span>
          </div>
          {progressPct !== null && (
            <div className="mt-2 h-1 bg-matrix-green/10 rounded overflow-hidden">
              <div
                className="h-full bg-neon-cyan/60 rounded transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Last run results */}
      <div className="border-t border-matrix-green/10 pt-2 space-y-1.5">
        <div className="flex justify-between">
          <span className="text-matrix-green/60">LAST RUN</span>
          <span className="text-matrix-green/80">{formatTime(ac.lastCompletedAt)}</span>
        </div>
        {ac.lastElapsed > 0 && (
          <div className="flex justify-between">
            <span className="text-matrix-green/60">DURATION</span>
            <span className="text-matrix-green/80">{formatDuration(ac.lastElapsed)}</span>
          </div>
        )}
        {(ac.lastIssuesCreated > 0 || ac.lastIssuesRetired > 0) && (
          <div className="flex justify-between">
            <span className="text-matrix-green/60">ISSUES</span>
            <span>
              <span className="text-matrix-green">+{ac.lastIssuesCreated} created</span>
              {ac.lastIssuesRetired > 0 && (
                <span className="text-matrix-green/50"> · -{ac.lastIssuesRetired} retired</span>
              )}
            </span>
          </div>
        )}
        {ac.lastStoriesGenerated > 0 && (
          <div className="flex justify-between">
            <span className="text-matrix-green/60">STORIES</span>
            <span className="text-matrix-green">{ac.lastStoriesGenerated} written</span>
          </div>
        )}
        {ac.lastBskyPosted > 0 && (
          <div className="flex justify-between">
            <span className="text-matrix-green/60">BLUESKY</span>
            <span className="text-neon-cyan">{ac.lastBskyPosted} posted</span>
          </div>
        )}
      </div>
    </div>
  );
}


// --- Main Admin Dashboard ---
function AdminDashboardView({
  token,
  onLogout,
}: {
  token: string;
  onLogout: () => void;
}) {
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<AdminPipelineStatus | null>(null);
  const [history, setHistory] = useState<PipelineRunInfo[]>([]);
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

  const loadDashboard = useCallback(async () => {
    try {
      const [d, h, s] = await Promise.all([
        fetchAdminDashboard(token),
        fetchAdminPipelineHistory(token),
        fetchAdminPipelineStatus(token),
      ]);
      setDashboard(d);
      setHistory(h);
      setPipelineStatus(s);
    } catch (e) {
      if (e instanceof Error && e.message === "Unauthorized") {
        onLogout();
      }
    } finally {
      setLoading(false);
    }
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
    const anyRunning = (pipelineStatus?.isRunning ?? false) || (pipelineStatus?.actionRefresh?.isRunning ?? false);
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
      <div className="min-h-screen bg-crt-black flex items-center justify-center">
        <span className="text-matrix-green font-terminal animate-pulse">
          Loading dashboard...
        </span>
      </div>
    );
  }

  const d = dashboard;

  return (
    <main className="min-h-screen bg-crt-black text-matrix-green px-4 py-8">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="font-pixel text-sm sm:text-lg tracking-widest">
            CIVITAS // ADMIN
          </h1>
          <button
            onClick={onLogout}
            aria-label="Log out of admin"
            className="text-[10px] font-pixel text-neon-pink/60 hover:text-neon-pink
                       border border-neon-pink/30 hover:border-neon-pink/60
                       px-3 py-1 rounded transition-colors"
          >
            [LOGOUT]
          </button>
        </div>

        {/* Completion banner */}
        {completionBanner && (
          <div
            role="status"
            aria-live="polite"
            className={`mb-6 border rounded p-4 flex items-center justify-between ${
              completionBanner.status === "completed"
                ? "border-matrix-green/60 bg-matrix-green/10"
                : "border-neon-pink/60 bg-neon-pink/10"
            }`}
          >
            <span
              className={`text-sm font-terminal font-bold ${
                completionBanner.status === "completed"
                  ? "text-matrix-green"
                  : "text-neon-pink"
              }`}
            >
              {completionBanner.status === "completed"
                ? "PIPELINE COMPLETED SUCCESSFULLY"
                : "PIPELINE FAILED"}
            </span>
            <span className="text-matrix-green/60 text-xs font-terminal">
              {completionBanner.duration}
            </span>
            <button
              onClick={() => setCompletionBanner(null)}
              className="text-matrix-green/40 hover:text-matrix-green text-xs ml-4"
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
                    Cache: {pipelineStatus.lastRun.cacheHits}H / {pipelineStatus.lastRun.cacheMisses}M
                  </span>
                  <span>Bills: {pipelineStatus.lastRun.billsClassified}</span>
                  <span>
                    Senators: {pipelineStatus.lastRun.senatorsProcessed}/{pipelineStatus.lastRun.senatorsTotal}
                    {pipelineStatus.lastRun.senatorsFailed > 0 && (
                      <span className="text-neon-pink ml-1">({pipelineStatus.lastRun.senatorsFailed}F)</span>
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
                  Reps: {pipelineStatus.houseLastRun.repsProcessed}/{pipelineStatus.houseLastRun.repsTotal}
                  {pipelineStatus.houseLastRun.repsFailed > 0 && (
                    <span className="text-neon-pink ml-1">({pipelineStatus.houseLastRun.repsFailed}F)</span>
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
                  {pipelineStatus.stockTradesLastRun.senateTradesIngested}S
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

        {/* Uptime Tracker */}
        <UptimeTracker uptime={d?.uptime} hostUptime={d?.host?.uptimeSeconds} />

        {/* System Monitor */}
        <SystemMonitor token={token} initialStats={d?.host} />

        {/* Seat Vacancy Control */}
        <VacancyControl token={token} />

        {/* Visitor Stats */}
        <VisitorStats token={token} />

        {/* System Health */}
        <div className="grid grid-cols-1 mb-6">
          {/* System Health */}
          <div className="terminal-window">
            <TerminalTitlebar title="system_health" />
            <div className="p-4 space-y-2 text-sm font-terminal">
              <div className="flex justify-between">
                <span className="text-matrix-green/60">DATABASE</span>
                <span className="flex items-center gap-2">
                  <StatusDot ok={d?.system.database === "ok"} />
                  {d?.system.database?.toUpperCase()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-matrix-green/60">OLLAMA</span>
                <span className="flex items-center gap-2">
                  <StatusDot ok={d?.system.ollama === "ok"} />
                  {d?.system.ollama?.toUpperCase()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-matrix-green/60">MODEL</span>
                <span className="text-neon-cyan">{d?.system.ollamaModel}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-matrix-green/60">DB SIZE</span>
                <span>{formatBytes(d?.system.dbSizeBytes ?? 0)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-matrix-green/60">VECTOR DB</span>
                <span className="flex items-center gap-2">
                  <StatusDot ok={d?.system.vectorDb?.status === "ok"} />
                  {d?.system.vectorDb?.status === "ok"
                    ? `${(d?.system.vectorDb?.totalVectors ?? 0).toLocaleString()} vectors / ${formatBytes(d?.system.vectorDb?.sizeBytes ?? 0)}`
                    : "UNAVAILABLE"}
                </span>
              </div>
              <div className="border-t border-matrix-green/15 pt-2 mt-2 space-y-1.5">
                {/* Senate pipeline */}
                <div className="flex justify-between">
                  <span className="text-matrix-green/60">SENATE</span>
                  <span className={pipelineStatus?.isRunning ? "text-neon-cyan animate-pulse" : "text-matrix-green/60"}>
                    {pipelineStatus?.isRunning ? "RUNNING" : (() => {
                      const run = pipelineStatus?.lastRun;
                      if (!run) return "IDLE";
                      const senators = `${run.senatorsProcessed}/${run.senatorsTotal}`;
                      const failed = run.senatorsFailed > 0 ? ` · ${run.senatorsFailed}F` : "";
                      return (
                        <span>
                          <span className={run.status === "completed" ? "text-matrix-green" : run.status === "failed" ? "text-neon-pink" : "text-matrix-green/60"}>
                            {run.status.toUpperCase()}
                          </span>
                          <span className="text-matrix-green/60"> · {senators}</span>
                          {run.senatorsFailed > 0 && <span className="text-neon-pink">{failed}</span>}
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
                    run?.status === "completed" ? "text-matrix-green" :
                    run?.status === "partial" ? "text-yellow-400" :
                    run?.status === "failed" ? "text-neon-pink" :
                    isStuck ? "text-yellow-400" : "text-matrix-green/50";
                  return (
                    <>
                      <PipelineStatusRow
                        label="HOUSE"
                        isRunning={!!pipelineStatus?.houseIsRunning}
                        run={run}
                        isStuck={isStuck}
                        statusClassName={statusClassName}
                        detail={run && (
                          <>
                            {run.repsTotal > 0 && <span className="text-matrix-green/60"> · {run.repsProcessed}/{run.repsTotal}</span>}
                            {(run.repsFailed ?? 0) > 0 && <span className="text-neon-pink"> · {run.repsFailed}F</span>}
                          </>
                        )}
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
                  const isStuck = run?.status === "running" && !pipelineStatus?.stockTradesIsRunning;
                  const statusClassName =
                    run?.status === "completed" ? "text-matrix-green" :
                    run?.status === "failed" ? "text-neon-pink" :
                    isStuck ? "text-yellow-400" : "text-matrix-green/50";
                  return (
                    <>
                      <PipelineStatusRow
                        label="STOCK TRADES"
                        isRunning={!!pipelineStatus?.stockTradesIsRunning}
                        run={run}
                        isStuck={isStuck}
                        statusClassName={statusClassName}
                        detail={run && <span className="text-matrix-green/60"> · {run.houseTradesIngested}H/{run.senateTradesIngested}S</span>}
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
                  const isStuck = run?.status === "running" && !pipelineStatus?.supplementaryIsRunning;
                  const statusClassName =
                    run?.status === "completed" ? "text-matrix-green" :
                    run?.status === "failed" ? "text-neon-pink" :
                    isStuck ? "text-yellow-400" : "text-matrix-green/50";
                  return (
                    <>
                      <PipelineStatusRow
                        label="SUPPLEMENTARY"
                        isRunning={!!pipelineStatus?.supplementaryIsRunning}
                        run={run}
                        isStuck={isStuck}
                        statusClassName={statusClassName}
                        detail={run && (
                          <span className="text-matrix-green/60">
                            {" "}· {run.exploreDocsIngested} docs · {run.justicesSkipped ? "SCOTUS skipped" : `${run.justicesScored} justices`} · {run.presidentsUpdated} pres
                          </span>
                        )}
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
                  <span className="text-matrix-green/60">SCHEDULE</span>
                  <span className="text-matrix-green/70">{d?.pipeline.cronSchedule}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-matrix-green/60">NEXT RUN</span>
                  <span className="text-matrix-green/70">
                    {formatTime(d?.pipeline.nextScheduled)}
                  </span>
                </div>
              </div>
            </div>
          </div>

        </div>

        {/* Action Center */}
        <div className="terminal-window mb-6">
          <TerminalTitlebar title="action_center" />
          <ActionCenterStatus ac={pipelineStatus?.actionRefresh ?? null} />
        </div>

        {/* Data Stats */}
        <div className="terminal-window mb-6">
          <TerminalTitlebar title="data_inventory" />
          {d && <DataInventory data={d.data} />}
        </div>

        {/* Vector DB & ML Metrics */}
        {d?.system.vectorDb && (
          <div className="terminal-window mb-6">
            <TerminalTitlebar title="vector_db_metrics" />
            <div className="p-4 space-y-4">
              {d.system.vectorDb.status !== "ok" ? (
                <p className="text-neon-pink text-xs font-terminal">
                  VECTOR DB UNAVAILABLE: {d.system.vectorDb.error}
                </p>
              ) : (
                <>
                  {/* Embedding Model */}
                  <div>
                    <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
                      EMBEDDING MODELS
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="border border-matrix-green/15 rounded p-3">
                        <div className="text-[10px] font-pixel text-matrix-green/40 mb-1">CLASSIFICATION MODEL</div>
                        <div className="text-xs font-terminal text-neon-cyan break-all">
                          {d.system.vectorDb.embeddingModel}
                        </div>
                        <div className="text-[10px] font-terminal text-matrix-green/50 mt-1">
                          v: {d.system.vectorDb.embeddingModelVersion}
                        </div>
                      </div>
                      <div className="border border-matrix-green/15 rounded p-3">
                        <div className="text-[10px] font-pixel text-matrix-green/40 mb-1">SEARCH INDEX MODEL</div>
                        <div className="text-sm font-terminal text-matrix-green">
                          {d.system.vectorDb.indexModelVersion || "rebuilding…"}
                        </div>
                      </div>
                      <div className="border border-matrix-green/15 rounded p-3">
                        <div className="text-[10px] font-pixel text-matrix-green/40 mb-1">DIMENSIONS</div>
                        <div className="text-sm font-terminal text-matrix-green">
                          {d.system.vectorDb.embeddingDimensions}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Collections */}
                  <div>
                    <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
                      COLLECTIONS ({d.system.vectorDb.collections?.length ?? 0})
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {d.system.vectorDb.collections?.map((col) => {
                        const pct = d.system.vectorDb!.totalVectors
                          ? Math.round((col.count / d.system.vectorDb!.totalVectors!) * 100)
                          : 0;
                        return (
                          <div
                            key={col.name}
                            className="border border-matrix-green/15 rounded p-3"
                          >
                            <div className="flex justify-between items-center mb-2">
                              <span className="text-xs font-terminal text-matrix-green">
                                {col.name}
                              </span>
                              <span className="text-xs font-terminal text-neon-cyan">
                                {col.count.toLocaleString()}
                              </span>
                            </div>
                            <div
                              className="w-full h-1.5 bg-matrix-green/10 rounded-full overflow-hidden mb-2"
                              role="progressbar"
                              aria-valuenow={pct}
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-label={`${col.name} vector count`}
                            >
                              <div
                                className="h-full bg-matrix-green/60 rounded-full transition-all"
                                style={{ width: `${Math.max(pct, 2)}%` }}
                              />
                            </div>
                            <div className="text-[10px] font-terminal text-matrix-green/40">
                              {pct}% of total vectors
                            </div>
                            {col.sampleMetadataKeys && col.sampleMetadataKeys.length > 0 && (
                              <div className="mt-1 text-[10px] font-terminal text-matrix-green/30">
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
                    <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
                      STORAGE
                    </h3>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <div className="border border-matrix-green/15 rounded p-3 text-center">
                        <div className="text-lg font-terminal text-matrix-green">
                          {(d.system.vectorDb.totalVectors ?? 0).toLocaleString()}
                        </div>
                        <div className="text-[10px] font-pixel text-matrix-green/50">TOTAL VECTORS</div>
                      </div>
                      <div className="border border-matrix-green/15 rounded p-3 text-center">
                        <div className="text-lg font-terminal text-matrix-green">
                          {formatBytes(d.system.vectorDb.sizeBytes ?? 0)}
                        </div>
                        <div className="text-[10px] font-pixel text-matrix-green/50">DISK SIZE</div>
                      </div>
                      <div className="border border-matrix-green/15 rounded p-3 text-center">
                        <div className="text-lg font-terminal text-matrix-green">
                          {d.system.vectorDb.collections?.length ?? 0}
                        </div>
                        <div className="text-[10px] font-pixel text-matrix-green/50">COLLECTIONS</div>
                      </div>
                      <div className="border border-matrix-green/15 rounded p-3 text-center">
                        <div className="text-lg font-terminal text-matrix-green">
                          {d.system.vectorDb.totalVectors && d.system.vectorDb.sizeBytes
                            ? `${Math.round(d.system.vectorDb.sizeBytes / d.system.vectorDb.totalVectors)} B`
                            : "—"}
                        </div>
                        <div className="text-[10px] font-pixel text-matrix-green/50">AVG PER VECTOR</div>
                      </div>
                    </div>
                  </div>

                  {/* Learning Store */}
                  {d.system.vectorDb.learningStore && !d.system.vectorDb.learningStore.error && (
                    <div>
                      <h3 className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mb-2">
                        LEARNING STORE
                      </h3>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                        <div className="border border-matrix-green/15 rounded p-3 text-center">
                          <div className="text-lg font-terminal text-matrix-green">
                            {d.system.vectorDb.learningStore.totalEntries.toLocaleString()}
                          </div>
                          <div className="text-[10px] font-pixel text-matrix-green/50">CLASSIFICATIONS</div>
                        </div>
                        <div className="border border-matrix-green/15 rounded p-3 text-center">
                          <div className="text-lg font-terminal text-matrix-green">
                            {d.system.vectorDb.learningStore.avgConfidence != null
                              ? `${(d.system.vectorDb.learningStore.avgConfidence * 100).toFixed(1)}%`
                              : "—"}
                          </div>
                          <div className="text-[10px] font-pixel text-matrix-green/50">AVG CONFIDENCE</div>
                        </div>
                        <div className="border border-matrix-green/15 rounded p-3 text-center">
                          <div className="text-lg font-terminal text-matrix-green">
                            {Object.keys(d.system.vectorDb.learningStore.bySource).length}
                          </div>
                          <div className="text-[10px] font-pixel text-matrix-green/50">SOURCES</div>
                        </div>
                        <div className="border border-matrix-green/15 rounded p-3 text-center">
                          <div className="text-lg font-terminal text-matrix-green">
                            {Object.keys(d.system.vectorDb.learningStore.byType).length}
                          </div>
                          <div className="text-[10px] font-pixel text-matrix-green/50">ENTITY TYPES</div>
                        </div>
                      </div>

                      {/* By Source breakdown */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="border border-matrix-green/15 rounded p-3">
                          <div className="text-[10px] font-pixel text-matrix-green/40 mb-2">BY SOURCE</div>
                          <div className="space-y-1.5">
                            {Object.entries(d.system.vectorDb.learningStore.bySource)
                              .sort(([, a], [, b]) => (b as number) - (a as number))
                              .map(([source, count]) => {
                                const total = d.system.vectorDb!.learningStore!.totalEntries;
                                const pct = total ? Math.round(((count as number) / total) * 100) : 0;
                                return (
                                  <div key={source}>
                                    <div className="flex justify-between text-xs font-terminal mb-0.5">
                                      <span className="text-matrix-green/70">{source}</span>
                                      <span className="text-matrix-green/50">
                                        {(count as number).toLocaleString()} ({pct}%)
                                      </span>
                                    </div>
                                    <div
                                      className="w-full h-1 bg-matrix-green/10 rounded-full overflow-hidden"
                                      role="progressbar"
                                      aria-valuenow={pct}
                                      aria-valuemin={0}
                                      aria-valuemax={100}
                                      aria-label={`${source} classifications`}
                                    >
                                      <div
                                        className="h-full bg-neon-cyan/50 rounded-full"
                                        style={{ width: `${Math.max(pct, 1)}%` }}
                                      />
                                    </div>
                                  </div>
                                );
                              })}
                          </div>
                        </div>
                        <div className="border border-matrix-green/15 rounded p-3">
                          <div className="text-[10px] font-pixel text-matrix-green/40 mb-2">BY ENTITY TYPE</div>
                          <div className="space-y-1.5">
                            {Object.entries(d.system.vectorDb.learningStore.byType)
                              .sort(([, a], [, b]) => (b as number) - (a as number))
                              .map(([type, count]) => {
                                const total = d.system.vectorDb!.learningStore!.totalEntries;
                                const pct = total ? Math.round(((count as number) / total) * 100) : 0;
                                return (
                                  <div key={type}>
                                    <div className="flex justify-between text-xs font-terminal mb-0.5">
                                      <span className="text-matrix-green/70">{type}</span>
                                      <span className="text-matrix-green/50">
                                        {(count as number).toLocaleString()} ({pct}%)
                                      </span>
                                    </div>
                                    <div
                                      className="w-full h-1 bg-matrix-green/10 rounded-full overflow-hidden"
                                      role="progressbar"
                                      aria-valuenow={pct}
                                      aria-valuemin={0}
                                      aria-valuemax={100}
                                      aria-label={`${type} classifications`}
                                    >
                                      <div
                                        className="h-full bg-matrix-green/40 rounded-full"
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
                      {Object.keys(d.system.vectorDb.learningStore.confidenceDistribution).length > 0 && (
                        <div className="border border-matrix-green/15 rounded p-3 mt-3">
                          <div className="text-[10px] font-pixel text-matrix-green/40 mb-2">
                            CONFIDENCE DISTRIBUTION
                          </div>
                          <div className="flex items-end gap-1 h-16">
                            {(() => {
                              const dist = d.system.vectorDb!.learningStore!.confidenceDistribution;
                              const buckets = Array.from({ length: 11 }, (_, i) => (i / 10).toFixed(1));
                              const maxCount = Math.max(
                                ...buckets.map((b) => (dist[b] ?? 0) as number),
                                1,
                              );
                              return buckets.map((bucket) => {
                                const count = (dist[bucket] ?? 0) as number;
                                const height = count > 0 ? Math.max((count / maxCount) * 100, 5) : 0;
                                return (
                                  <div
                                    key={bucket}
                                    className="flex-1 flex flex-col items-center gap-0.5"
                                    title={`${bucket}: ${count} entries`}
                                  >
                                    <div className="w-full flex items-end justify-center" style={{ height: "48px" }}>
                                      <div
                                        className="w-full rounded-t bg-neon-cyan/40 transition-all min-w-[4px]"
                                        style={{ height: `${height}%` }}
                                      />
                                    </div>
                                    <span className="text-[8px] font-terminal text-matrix-green/30">
                                      {bucket}
                                    </span>
                                  </div>
                                );
                              });
                            })()}
                          </div>
                        </div>
                      )}

                      {/* Timestamps */}
                      <div className="flex gap-4 mt-2 text-[10px] font-terminal text-matrix-green/40">
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
          <div className="terminal-window mb-6">
            <TerminalTitlebar title="last_run_detail" />
            <div className="p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-terminal">
                <div>
                  <span className="text-matrix-green/50 text-xs block">STATUS</span>
                  <span
                    className={
                      d.pipeline.lastRun.status === "completed"
                        ? "text-matrix-green"
                        : d.pipeline.lastRun.status === "failed"
                          ? "text-neon-pink"
                          : "text-neon-cyan"
                    }
                  >
                    {d.pipeline.lastRun.status.toUpperCase()}
                  </span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">STARTED</span>
                  <span>{formatTime(d.pipeline.lastRun.startedAt)}</span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">DURATION</span>
                  <span>{formatDuration(d.pipeline.lastRun.elapsedSeconds)}</span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">SENATORS</span>
                  <span>
                    {d.pipeline.lastRun.senatorsProcessed}/{d.pipeline.lastRun.senatorsTotal}
                    {d.pipeline.lastRun.senatorsFailed > 0 && (
                      <span className="text-neon-pink ml-1">
                        ({d.pipeline.lastRun.senatorsFailed} failed)
                      </span>
                    )}
                  </span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">LLM CALLS</span>
                  <span>{d.pipeline.lastRun.llmCalls}</span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">BILLS CLASSIFIED</span>
                  <span>{d.pipeline.lastRun.billsClassified}</span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">CACHE HIT RATE</span>
                  <span>
                    {d.pipeline.lastRun.cacheHits + d.pipeline.lastRun.cacheMisses > 0
                      ? `${Math.round(
                          (d.pipeline.lastRun.cacheHits /
                            (d.pipeline.lastRun.cacheHits + d.pipeline.lastRun.cacheMisses)) *
                            100,
                        )}%`
                      : "—"}{" "}
                    <span className="text-matrix-green/40">
                      ({d.pipeline.lastRun.cacheHits}H / {d.pipeline.lastRun.cacheMisses}M)
                    </span>
                  </span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">PIPELINE RUNS</span>
                  <span>
                    {d.pipeline.totalRuns} total
                    <span className="text-matrix-green/40">
                      {" "}({d.pipeline.successfulRuns}✓ {d.pipeline.failedRuns}✗)
                    </span>
                  </span>
                </div>
              </div>
              {d.pipeline.lastRun.errorMessage && (
                <div className="mt-3 p-2 border border-neon-pink/30 rounded bg-neon-pink/5">
                  <span className="text-neon-pink text-xs font-terminal">
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
                <span className="text-matrix-green/50 text-xs block">REPS</span>
                <span>
                  {pipelineStatus.houseLastRun.repsProcessed}/{pipelineStatus.houseLastRun.repsTotal}
                  {pipelineStatus.houseLastRun.repsFailed > 0 && (
                    <span className="text-neon-pink ml-1">
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
                <span className="text-matrix-green/50 text-xs block">TRADES INGESTED</span>
                <span>
                  {pipelineStatus.stockTradesLastRun.houseTradesIngested}H /{" "}
                  {pipelineStatus.stockTradesLastRun.senateTradesIngested}S
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
                  <span className="text-matrix-green/50 text-xs block">EXPLORE DOCS</span>
                  <span>{pipelineStatus.supplementaryLastRun.exploreDocsIngested}</span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">SCOTUS</span>
                  <span>
                    {pipelineStatus.supplementaryLastRun.justicesSkipped
                      ? "skipped"
                      : `${pipelineStatus.supplementaryLastRun.justicesScored} scored`}
                  </span>
                </div>
                <div>
                  <span className="text-matrix-green/50 text-xs block">PRESIDENTS</span>
                  <span>{pipelineStatus.supplementaryLastRun.presidentsUpdated}</span>
                </div>
              </>
            )
          }
        />

        {/* LLM Stats */}
        {d?.llm && Object.keys(d.llm).length > 0 && (
          <div className="terminal-window mb-6">
            <TerminalTitlebar title="llm_stats" />
            <div className="p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-terminal">
                {Object.entries(d.llm)
                  .filter(([, val]) => val === null || typeof val !== "object")
                  .map(([key, val]) => (
                    <div key={key}>
                      <span className="text-matrix-green/50 text-xs block">
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
        <div className="terminal-window mb-6">
          <TerminalTitlebar title="pipeline_history" />
          <div className="p-4">
            <RunHistory runs={history} />
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-matrix-green/30 text-xs font-terminal mt-8">
          <button
            onClick={loadDashboard}
            className="text-matrix-green/40 hover:text-matrix-green transition-colors"
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
  const [token, setToken] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const stored = sessionStorage.getItem(TOKEN_KEY);
    if (stored) {
      setToken(stored);
    }
    setChecked(true);
  }, []);

  const handleLogin = (t: string) => {
    sessionStorage.setItem(TOKEN_KEY, t);
    setToken(t);
  };

  const handleLogout = () => {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(null);
  };

  if (!checked) return null;

  if (!token) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return <AdminDashboardView token={token} onLogout={handleLogout} />;
}
