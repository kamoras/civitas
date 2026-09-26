"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useSessionToken } from "@/hooks/useSessionToken";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import {
  adminAuth,
  fetchAdminDashboard,
  fetchAdminPipelineStatus,
  fetchAdminPipelineHistory,
  type AdminDashboard,
  type AdminPipelineStatus,
  type PipelineHistoryRun,
} from "@/lib/api";
import { tabControl } from "@/lib/controlStyles";
import { ActionCenterDashboard } from "@/components/admin/ActionCenterDashboard";
import { ActivePipelines } from "@/components/admin/ActivePipelines";
import { DataDashboard } from "@/components/admin/DataDashboard";
import { formatDuration } from "@/components/admin/format";
import { OverviewDashboard } from "@/components/admin/OverviewDashboard";
import { PipelinesDashboard } from "@/components/admin/PipelinesDashboard";
import { SystemDashboard } from "@/components/admin/SystemDashboard";
import { TrafficDashboard } from "@/components/admin/TrafficDashboard";
import { useHostHistory } from "@/components/admin/useHostHistory";
import { VacancyControl } from "@/components/admin/VacancyControl";

const TOKEN_KEY = "civitas_admin_token";

// The dashboard used to be one ~20-panel column. Each tab is now one
// question an operator comes with: who is using the site and how fast is it
// (Traffic), are the nightly runs healthy (Pipelines), is the hourly news
// refresh publishing or suppressing (Action Center), what is in the stores
// (Data & ML), is the box itself OK (System), and the one write action
// (Tools). Overview summarises each and links through.
const TABS = [
  { id: "overview", label: "Overview" },
  { id: "traffic", label: "Traffic" },
  { id: "pipelines", label: "Pipelines" },
  { id: "action", label: "Action Center" },
  { id: "data", label: "Data & ML" },
  { id: "system", label: "System" },
  { id: "tools", label: "Tools" },
] as const;

type Tab = (typeof TABS)[number]["id"];

function isTab(v: string | null): v is Tab {
  return TABS.some((t) => t.id === v);
}

function tabFromLocation(): Tab {
  const t = new URLSearchParams(window.location.search).get("tab");
  return isTab(t) ? t : "overview";
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

// --- Main Admin Dashboard ---
function AdminDashboardView({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<AdminPipelineStatus | null>(null);
  const [history, setHistory] = useState<PipelineHistoryRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [completionBanner, setCompletionBanner] = useState<{
    label: string;
    status: "completed" | "failed";
    duration: string;
  } | null>(null);
  // Read once from the URL the page was opened with (this view only renders
  // client-side, after the session token loads, so window is available).
  const [tab, setTab] = useState<Tab>(tabFromLocation);
  const { stats: hostStats, history: hostHistory } = useHostHistory(token);

  const wasRunningRef = useRef<Record<string, boolean>>({});
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Tabs write ?tab= through the History API, not router.replace(): on a
  // statically prerendered route the latter silently does nothing once the
  // page was loaded with a query string (see AGENTS.md, "Client-side URL
  // state"). pushState, so Back returns to the previous tab.
  const selectTab = useCallback((next: Tab) => {
    setTab(next);
    const url = next === "overview" ? "/admin" : `/admin?tab=${next}`;
    if (window.location.pathname + window.location.search !== url) {
      window.history.pushState(null, "", url);
    }
    // Focus the incoming *tab*, not its panel: the Arrow/Home/End handler
    // lives on the tablist, so moving focus into the panel would strand the
    // keyboard after one press.
    requestAnimationFrame(() => document.getElementById(`admin-tab-${next}`)?.focus());
  }, []);

  useEffect(() => {
    const onPop = () => setTab(tabFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const onTabKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const ids = TABS.map((t) => t.id);
    const idx = ids.indexOf(tab);
    const next =
      e.key === "ArrowRight"
        ? ids[(idx + 1) % ids.length]
        : e.key === "ArrowLeft"
          ? ids[(idx - 1 + ids.length) % ids.length]
          : e.key === "Home"
            ? ids[0]
            : e.key === "End"
              ? ids[ids.length - 1]
              : null;
    if (!next) return;
    e.preventDefault();
    selectTab(next);
  };

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

  // Announce a finished run for every pipeline, not just Senate: this used to
  // watch only `isRunning` (Senate's flag), so a House or Election run ending
  // never raised the banner or refreshed the page's counts.
  const pollStatus = useCallback(async () => {
    try {
      const s = await fetchAdminPipelineStatus(token);
      setPipelineStatus(s);

      const watched = [
        { key: "senate", label: "SENATE", running: s.isRunning, run: s.lastRun },
        { key: "house", label: "HOUSE", running: !!s.houseIsRunning, run: s.houseLastRun },
        {
          key: "supplementary",
          label: "SUPPLEMENTARY",
          running: !!s.supplementaryIsRunning,
          run: s.supplementaryLastRun,
        },
        {
          key: "stock_trades",
          label: "STOCK TRADES",
          running: !!s.stockTradesIsRunning,
          run: s.stockTradesLastRun,
        },
        {
          key: "election",
          label: "ELECTION",
          running: !!s.electionIsRunning,
          run: s.electionLastRun,
        },
      ];
      const finished = watched.find((w) => !w.running && wasRunningRef.current[w.key]);
      if (finished) {
        setCompletionBanner({
          label: finished.label,
          status: finished.run?.status === "failed" ? "failed" : "completed",
          duration: formatDuration(finished.run?.elapsedSeconds),
        });
        setTimeout(() => setCompletionBanner(null), 15000);
        loadDashboard();
      }
      wasRunningRef.current = Object.fromEntries(watched.map((w) => [w.key, w.running]));
    } catch {}
  }, [token, loadDashboard]);

  const dashboardPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  const anyPipelineRunning = !!(
    pipelineStatus?.isRunning ||
    pipelineStatus?.houseIsRunning ||
    pipelineStatus?.supplementaryIsRunning ||
    pipelineStatus?.stockTradesIsRunning ||
    pipelineStatus?.electionIsRunning
  );
  const actionRunning = pipelineStatus?.actionRefresh?.isRunning ?? false;

  useEffect(() => {
    const interval = anyPipelineRunning || actionRunning ? 3000 : 10000;

    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(pollStatus, interval);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [pollStatus, anyPipelineRunning, actionRunning]);

  useEffect(() => {
    const interval = anyPipelineRunning ? 10000 : 30000;

    if (dashboardPollRef.current) clearInterval(dashboardPollRef.current);
    dashboardPollRef.current = setInterval(loadDashboard, interval);
    return () => {
      if (dashboardPollRef.current) clearInterval(dashboardPollRef.current);
    };
  }, [loadDashboard, anyPipelineRunning]);

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
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="font-mono text-sm sm:text-lg tracking-widest">CIVITAS // ADMIN</h1>
          <div className="flex items-center gap-3">
            <button
              onClick={loadDashboard}
              className="text-xs font-mono text-ink-min hover:text-ink-lo transition-colors"
            >
              [REFRESH]
            </button>
            <button
              onClick={onLogout}
              aria-label="Log out of admin"
              className="text-xs font-mono text-ink-lo hover:text-signal-magenta border border-signal-magenta/40 hover:border-signal-magenta/40
                         px-3 py-1  transition-colors"
            >
              [LOGOUT]
            </button>
          </div>
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
                ? `${completionBanner.label} PIPELINE COMPLETED`
                : `${completionBanner.label} PIPELINE FAILED`}
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

        <ActivePipelines pipelineStatus={pipelineStatus} />

        {/* overflow-y-hidden: overflow-x-auto makes overflow-y auto too, and a
            fractional-pixel tab height is then enough to raise a phosphor
            scrollbar beside the row (see the Action Center's tab bar). */}
        <div
          role="tablist"
          aria-label="Admin dashboards"
          className="sticky top-0 z-30 -mx-4 mb-6 flex overflow-x-auto overflow-y-hidden border-b border-white/15 bg-surface-base/95 px-4 backdrop-blur-sm sm:mx-0 sm:px-0"
          onKeyDown={onTabKeyDown}
        >
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`admin-tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`admin-tabpanel-${t.id}`}
              tabIndex={tab === t.id ? 0 : -1}
              onClick={() => selectTab(t.id)}
              className={`-mb-px whitespace-nowrap border-b-3 px-3 py-3 font-mono text-xs uppercase tracking-[0.14em] transition-colors sm:px-4 ${tabControl(
                tab === t.id
              )}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div
          role="tabpanel"
          id={`admin-tabpanel-${tab}`}
          aria-labelledby={`admin-tab-${tab}`}
          tabIndex={0}
          className="outline-none"
        >
          {tab === "overview" && (
            <OverviewDashboard
              token={token}
              dashboard={d}
              status={pipelineStatus}
              host={hostStats}
              goTo={selectTab}
            />
          )}
          {tab === "traffic" && <TrafficDashboard token={token} />}
          {tab === "pipelines" && (
            <PipelinesDashboard
              token={token}
              status={pipelineStatus}
              dashboard={d}
              history={history}
              onChanged={pollStatus}
            />
          )}
          {tab === "action" && (
            <ActionCenterDashboard token={token} ac={pipelineStatus?.actionRefresh ?? null} />
          )}
          {tab === "data" && <DataDashboard d={d} />}
          {tab === "system" && (
            <SystemDashboard dashboard={d} stats={hostStats} history={hostHistory} />
          )}
          {tab === "tools" && <VacancyControl token={token} />}
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
