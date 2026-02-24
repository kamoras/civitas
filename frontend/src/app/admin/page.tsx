"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  adminAuth,
  fetchAdminDashboard,
  fetchAdminPipelineStatus,
  fetchAdminPipelineHistory,
  fetchAdminSystemStats,
  triggerAdminPipeline,
  triggerAdminExplorePipeline,
  type AdminDashboard,
  type AdminPipelineStatus,
  type HostStats,
  type PipelineRunInfo,
} from "@/lib/api";

const PHASE_LABELS: Record<string, string> = {
  fetch: "FETCHING DATA",
  transform: "TRANSFORMING",
  analyze: "ANALYZING",
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
  return new Date(iso.endsWith("Z") ? iso : iso + "Z");
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
          <div className="terminal-titlebar" aria-hidden="true">
            <span className="terminal-dot red" />
            <span className="terminal-dot yellow" />
            <span className="terminal-dot green" />
            <span className="ml-3 text-white/40 text-xs font-terminal">
              admin_auth.sh
            </span>
          </div>
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
              />
              {error && (
                <p className="text-neon-pink text-xs mt-2">{error}</p>
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
        setRate(`${secPer.toFixed(0)}s/senator`);
        setEta(formatEtaSeconds(Math.round(remaining * secPer)));
      } else if (processed > 0 && elapsedSeconds && elapsedSeconds > 0) {
        const secPer = elapsedSeconds / processed;
        setRate(`~${secPer.toFixed(0)}s/senator`);
        setEta(formatEtaSeconds(Math.round(remaining * secPer)));
      } else {
        setEta(null);
        setRate(null);
      }
    };

    tick();
    const id = setInterval(tick, 2000);
    return () => clearInterval(id);
  }, [isAnalyze, processed, total, elapsedSeconds]);

  return { eta, rate };
}

function PipelineProgressBar({ status }: { status: AdminPipelineStatus }) {
  const run = status.lastRun;
  const phase = run?.currentPhase ?? "fetch";
  const total = run?.senatorsTotal ?? 0;
  const processed = run?.senatorsProcessed ?? 0;
  const elapsed = run?.elapsedSeconds ?? null;
  const isAnalyze = status.isRunning && phase === "analyze" && total > 0;
  const pct = isAnalyze ? Math.round((processed / total) * 100) : null;
  const { eta, rate } = useAnalyzeEta(isAnalyze, processed, total, elapsed);

  if (!status.isRunning || !run) return null;

  return (
    <div className="border border-neon-cyan/40 rounded p-4 bg-neon-cyan/5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-neon-cyan text-sm font-terminal font-bold flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-neon-cyan animate-pulse" />
          PIPELINE ACTIVE
        </span>
        <span className="text-matrix-green/70 text-xs font-terminal">
          <ElapsedTimer startedAt={run.startedAt} />
        </span>
      </div>

      <PhaseSteps currentPhase={phase} />

      <div className="mt-3">
        {isAnalyze && (
          <div className="flex items-center justify-between mb-1">
            <span className="text-matrix-green/60 text-[10px] font-terminal">
              PROCESSING SENATORS
            </span>
            <span className="text-matrix-green text-xs font-terminal tabular-nums">
              {processed} / {total} ({pct}%)
            </span>
          </div>
        )}
        <div
          className="w-full h-2 bg-matrix-green/10 border border-matrix-green/20 rounded-sm overflow-hidden"
          role="progressbar"
          aria-valuenow={pct ?? undefined}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          {isAnalyze ? (
            <div
              className="h-full bg-neon-cyan transition-all duration-700"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <div className="h-full w-full relative overflow-hidden">
              <div className="absolute inset-0 bg-neon-cyan/20" />
              <div
                className="absolute inset-y-0 w-1/3 bg-neon-cyan/60"
                style={{ animation: "pipeline-scan 1.6s ease-in-out infinite" }}
              />
            </div>
          )}
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

      <div className="flex gap-4 mt-2 text-[10px] text-matrix-green/50 font-terminal">
        <span>LLM: {run.llmCalls}</span>
        <span>Cache: {run.cacheHits}H / {run.cacheMisses}M</span>
        <span>Bills: {run.billsClassified}</span>
        <span>
          Senators: {run.senatorsProcessed}/{run.senatorsTotal}
          {run.senatorsFailed > 0 && (
            <span className="text-neon-pink ml-1">({run.senatorsFailed}F)</span>
          )}
        </span>
      </div>

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
}: {
  pct: number;
  warnAt?: number;
  critAt?: number;
}) {
  const color =
    pct >= critAt
      ? "bg-neon-pink"
      : pct >= warnAt
        ? "bg-neon-yellow"
        : "bg-matrix-green";
  return (
    <div className="w-full h-1.5 bg-matrix-green/10 rounded-sm overflow-hidden">
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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
    pollRef.current = setInterval(poll, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
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
      <div className="terminal-titlebar" aria-hidden="true">
        <span className="terminal-dot red" />
        <span className="terminal-dot yellow" />
        <span className="terminal-dot green" />
        <span className="ml-3 text-white/40 text-xs font-terminal">
          system_monitor
        </span>
        <span className="ml-auto text-white/20 text-[10px] font-terminal mr-2">
          live
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-matrix-green ml-1 animate-pulse" />
        </span>
      </div>
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
            <UsageBar pct={loadPct} />
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
            <UsageBar pct={stats.memUsedPct} />
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
            <UsageBar pct={stats.diskUsedPct} warnAt={80} critAt={95} />
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
              <UsageBar pct={(stats.cpuTempC / 85) * 100} warnAt={76} critAt={94} />
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
                  <div className="w-full h-1 rounded-full bg-matrix-green/10 mt-0.5">
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
                  <div className="w-full h-1 rounded-full bg-matrix-green/10 mt-0.5">
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

// --- Run History Table ---
function RunHistory({ runs }: { runs: PipelineRunInfo[] }) {
  if (runs.length === 0) return <p className="text-matrix-green/40 text-xs">No pipeline runs recorded.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-terminal">
        <thead>
          <tr className="text-matrix-green/60 border-b border-matrix-green/20">
            <th scope="col" className="text-left py-1 pr-3">#</th>
            <th scope="col" className="text-left py-1 pr-3">STARTED</th>
            <th scope="col" className="text-left py-1 pr-3">STATUS</th>
            <th scope="col" className="text-left py-1 pr-3">DURATION</th>
            <th scope="col" className="text-right py-1 pr-3">SENATORS</th>
            <th scope="col" className="text-right py-1 pr-3">LLM</th>
            <th scope="col" className="text-right py-1">CACHE</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.id}
              className="border-b border-matrix-green/10 hover:bg-matrix-green/5"
            >
              <td className="py-1.5 pr-3 text-matrix-green/40">{r.id}</td>
              <td className="py-1.5 pr-3 text-matrix-green/70">{formatTime(r.startedAt)}</td>
              <td className="py-1.5 pr-3">
                <span
                  className={
                    r.status === "completed"
                      ? "text-matrix-green"
                      : r.status === "failed"
                        ? "text-neon-pink"
                        : r.status === "running"
                          ? "text-neon-cyan animate-pulse"
                          : "text-matrix-green/50"
                  }
                >
                  {r.status.toUpperCase()}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-matrix-green/60">{formatDuration(r.elapsedSeconds)}</td>
              <td className="py-1.5 pr-3 text-right text-matrix-green/60">
                {r.senatorsProcessed}/{r.senatorsTotal}
                {r.senatorsFailed > 0 && (
                  <span className="text-neon-pink ml-1">({r.senatorsFailed}F)</span>
                )}
              </td>
              <td className="py-1.5 pr-3 text-right text-matrix-green/60">{r.llmCalls}</td>
              <td className="py-1.5 text-right text-matrix-green/60">
                {r.cacheHits + r.cacheMisses > 0
                  ? `${Math.round((r.cacheHits / (r.cacheHits + r.cacheMisses)) * 100)}%`
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
  const [triggerMsg, setTriggerMsg] = useState("");
  const [triggerError, setTriggerError] = useState("");
  const [triggering, setTriggering] = useState(false);
  const [completionBanner, setCompletionBanner] = useState<{
    status: "completed" | "failed";
    duration: string;
  } | null>(null);

  const wasRunningRef = useRef(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const triggerMsgTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const [d, h] = await Promise.all([
        fetchAdminDashboard(token),
        fetchAdminPipelineHistory(token),
      ]);
      setDashboard(d);
      setHistory(h);
      setPipelineStatus({
        isRunning: d.pipeline.isRunning,
        lastRun: d.pipeline.lastRun,
      });
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
    const isRunning = pipelineStatus?.isRunning ?? false;
    const interval = isRunning ? 2000 : 10000;

    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(pollStatus, interval);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [pollStatus, pipelineStatus?.isRunning]);

  useEffect(() => {
    const isRunning = pipelineStatus?.isRunning ?? false;
    const interval = isRunning ? 10000 : 30000;

    if (dashboardPollRef.current) clearInterval(dashboardPollRef.current);
    dashboardPollRef.current = setInterval(loadDashboard, interval);
    return () => {
      if (dashboardPollRef.current) clearInterval(dashboardPollRef.current);
    };
  }, [loadDashboard, pipelineStatus?.isRunning]);

  const handleTrigger = async (type: "full" | "fetch" | "explore") => {
    setTriggerMsg("");
    setTriggerError("");
    setCompletionBanner(null);
    setTriggering(true);
    try {
      if (type === "explore") {
        const r = await triggerAdminExplorePipeline(token);
        setTriggerMsg(r.message);
      } else {
        const r = await triggerAdminPipeline(token, {
          fetchOnly: type === "fetch",
        });
        setTriggerMsg(r.message);
        setPipelineStatus((prev) => (prev ? { ...prev, isRunning: true } : prev));
        wasRunningRef.current = true;
      }

      if (triggerMsgTimeoutRef.current) clearTimeout(triggerMsgTimeoutRef.current);
      triggerMsgTimeoutRef.current = setTimeout(() => setTriggerMsg(""), 8000);

      setTimeout(pollStatus, 1000);
    } catch (e) {
      setTriggerError(e instanceof Error ? e.message : "Trigger failed");
    } finally {
      setTriggering(false);
    }
  };

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
    <div className="min-h-screen bg-crt-black text-matrix-green px-4 py-8">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <h1 className="font-pixel text-sm sm:text-lg tracking-widest">
            CIVITAS // ADMIN
          </h1>
          <button
            onClick={onLogout}
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

        {/* Live pipeline progress */}
        {pipelineStatus && pipelineStatus.isRunning && (
          <div className="mb-6">
            <PipelineProgressBar status={pipelineStatus} />
          </div>
        )}

        {/* System Monitor */}
        <SystemMonitor token={token} initialStats={d?.host} />

        {/* System Health + Pipeline Controls */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          {/* System Health */}
          <div className="terminal-window">
            <div className="terminal-titlebar" aria-hidden="true">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="ml-3 text-white/40 text-xs font-terminal">system_health</span>
            </div>
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
              <div className="border-t border-matrix-green/15 pt-2 mt-2">
                <div className="flex justify-between">
                  <span className="text-matrix-green/60">PIPELINE</span>
                  <span className={pipelineStatus?.isRunning ? "text-neon-cyan animate-pulse" : "text-matrix-green/60"}>
                    {pipelineStatus?.isRunning ? "RUNNING" : "IDLE"}
                  </span>
                </div>
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

          {/* Pipeline Controls */}
          <div className="terminal-window">
            <div className="terminal-titlebar" aria-hidden="true">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="ml-3 text-white/40 text-xs font-terminal">pipeline_control</span>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-matrix-green/50 text-xs font-terminal mb-2">
                Trigger pipeline runs manually:
              </p>
              <div className="space-y-2">
                <button
                  onClick={() => handleTrigger("full")}
                  disabled={pipelineStatus?.isRunning || triggering}
                  className="w-full text-xs font-pixel py-2.5 rounded border transition-all
                             text-matrix-green border-matrix-green/40 hover:bg-matrix-green/10 hover:border-matrix-green/60
                             disabled:text-matrix-green/20 disabled:border-matrix-green/15 disabled:cursor-not-allowed"
                >
                  {triggering
                    ? "LAUNCHING..."
                    : pipelineStatus?.isRunning
                      ? "PIPELINE RUNNING..."
                      : "▶ RUN FULL PIPELINE"}
                </button>
                <button
                  onClick={() => handleTrigger("fetch")}
                  disabled={pipelineStatus?.isRunning || triggering}
                  className="w-full text-xs font-pixel py-2.5 rounded border transition-all
                             text-neon-cyan/70 border-neon-cyan/30 hover:bg-neon-cyan/10 hover:border-neon-cyan/50
                             disabled:text-matrix-green/20 disabled:border-matrix-green/15 disabled:cursor-not-allowed"
                >
                  ▶ FETCH ONLY (NO LLM)
                </button>
                <button
                  onClick={() => handleTrigger("explore")}
                  disabled={triggering}
                  className="w-full text-xs font-pixel py-2.5 rounded border transition-all
                             text-neon-yellow/70 border-neon-yellow/30 hover:bg-neon-yellow/10 hover:border-neon-yellow/50
                             disabled:text-matrix-green/20 disabled:border-matrix-green/15 disabled:cursor-not-allowed"
                >
                  ▶ RE-INDEX EXPLORE DOCS
                </button>
              </div>
              {triggerMsg && (
                <div className="mt-3 p-2 border border-matrix-green/30 rounded bg-matrix-green/10
                                animate-[fadeIn_0.3s_ease-out]">
                  <p className="text-matrix-green text-xs font-terminal font-bold">
                    ✓ {triggerMsg}
                  </p>
                  <p className="text-matrix-green/50 text-[10px] font-terminal mt-1">
                    Status updates will appear automatically above.
                  </p>
                </div>
              )}
              {triggerError && (
                <div className="mt-3 p-2 border border-neon-pink/30 rounded bg-neon-pink/10
                                animate-[fadeIn_0.3s_ease-out]">
                  <p className="text-neon-pink text-xs font-terminal font-bold">
                    ✗ {triggerError}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Data Stats */}
        <div className="terminal-window mb-6">
          <div className="terminal-titlebar" aria-hidden="true">
            <span className="terminal-dot red" />
            <span className="terminal-dot yellow" />
            <span className="terminal-dot green" />
            <span className="ml-3 text-white/40 text-xs font-terminal">data_inventory</span>
          </div>
          <div className="p-4">
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {d &&
                Object.entries(d.data).map(([key, count]) => (
                  <div
                    key={key}
                    className="border border-matrix-green/15 rounded p-3 text-center"
                  >
                    <div className="text-lg font-terminal text-matrix-green">
                      {count.toLocaleString()}
                    </div>
                    <div className="text-[10px] font-pixel text-matrix-green/50 tracking-wider mt-1">
                      {key.replace(/([A-Z])/g, " $1").toUpperCase().trim()}
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </div>

        {/* Vector DB & ML Metrics */}
        {d?.system.vectorDb && (
          <div className="terminal-window mb-6">
            <div className="terminal-titlebar" aria-hidden="true">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="ml-3 text-white/40 text-xs font-terminal">vector_db_metrics</span>
            </div>
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
                      EMBEDDING MODEL
                    </h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div className="border border-matrix-green/15 rounded p-3">
                        <div className="text-[10px] font-pixel text-matrix-green/40 mb-1">MODEL</div>
                        <div className="text-xs font-terminal text-neon-cyan break-all">
                          {d.system.vectorDb.embeddingModel}
                        </div>
                      </div>
                      <div className="border border-matrix-green/15 rounded p-3">
                        <div className="text-[10px] font-pixel text-matrix-green/40 mb-1">VERSION</div>
                        <div className="text-sm font-terminal text-matrix-green">
                          {d.system.vectorDb.embeddingModelVersion}
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
                            <div className="w-full h-1.5 bg-matrix-green/10 rounded-full overflow-hidden mb-2">
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
                                    <div className="w-full h-1 bg-matrix-green/10 rounded-full overflow-hidden">
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
                                    <div className="w-full h-1 bg-matrix-green/10 rounded-full overflow-hidden">
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
            <div className="terminal-titlebar" aria-hidden="true">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="ml-3 text-white/40 text-xs font-terminal">last_run_detail</span>
            </div>
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
            </div>
          </div>
        )}

        {/* LLM Stats */}
        {d?.llm && Object.keys(d.llm).length > 0 && (
          <div className="terminal-window mb-6">
            <div className="terminal-titlebar" aria-hidden="true">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="ml-3 text-white/40 text-xs font-terminal">llm_stats</span>
            </div>
            <div className="p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm font-terminal">
                {Object.entries(d.llm).map(([key, val]) => (
                  <div key={key}>
                    <span className="text-matrix-green/50 text-xs block">
                      {key.replace(/_/g, " ").toUpperCase()}
                    </span>
                    <span>{String(val)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Pipeline History */}
        <div className="terminal-window mb-6">
          <div className="terminal-titlebar" aria-hidden="true">
            <span className="terminal-dot red" />
            <span className="terminal-dot yellow" />
            <span className="terminal-dot green" />
            <span className="ml-3 text-white/40 text-xs font-terminal">pipeline_history</span>
          </div>
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
    </div>
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
