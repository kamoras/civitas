"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { localDateStr, formatUtcDate } from "@/lib/formatting";

const STORAGE_KEY = "civitas_actions";

export interface CivicAction {
  id: string;
  issue_title: string;
  action_text: string;
  date: string; // YYYY-MM-DD
  senator_name?: string;
}

function today(): string {
  return localDateStr();
}

function loadActions(): CivicAction[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as CivicAction[];
  } catch {
    return [];
  }
}

function saveActions(actions: CivicAction[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(actions));
}

function computeStreak(actions: CivicAction[]): number {
  if (actions.length === 0) return 0;
  const days = new Set(actions.map((a) => a.date));
  let streak = 0;
  const d = new Date();
  // count back from today; allow today to count even if no action yet today
  for (let i = 0; i < 365; i++) {
    const key = localDateStr(d);
    if (days.has(key)) {
      streak++;
      d.setDate(d.getDate() - 1);
    } else if (i === 0) {
      // today with no action yet — don't break streak, just skip
      d.setDate(d.getDate() - 1);
    } else {
      break;
    }
  }
  return streak;
}

const formatDate = (dateStr: string): string =>
  formatUtcDate(dateStr, { month: "short", day: "numeric" }, "en-US");

// Custom event so LogActionButton can notify the widget without prop drilling
const TRACKER_EVENT = "civitas:action-logged";

export function dispatchActionLogged(): void {
  window.dispatchEvent(new Event(TRACKER_EVENT));
}

function subscribeActions(callback: () => void): () => void {
  window.addEventListener(TRACKER_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(TRACKER_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

// Whether this issue was already logged today (localStorage-backed). A boolean
// useSyncExternalStore snapshot — updated via TRACKER_EVENT on log — so no
// read-into-state effect.
function useLoggedToday(issueTitle: string): boolean {
  return useSyncExternalStore(
    subscribeActions,
    () => loadActions().some((a) => a.issue_title === issueTitle && a.date === today()),
    () => false
  );
}

// ── Log Action Button ─────────────────────────────────────────────────────────

export function LogActionButton({
  issueTitle,
  senatorName,
}: {
  issueTitle: string;
  senatorName?: string;
}) {
  const [showForm, setShowForm] = useState(false);
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const logged = useLoggedToday(issueTitle);

  useEffect(() => {
    if (showForm) inputRef.current?.focus();
  }, [showForm]);

  function handleLog() {
    const actionText = text.trim() || "Took action on this issue";
    const actions = loadActions();
    actions.unshift({
      id: Date.now().toString(),
      issue_title: issueTitle,
      action_text: actionText,
      date: today(),
      senator_name: senatorName,
    });
    saveActions(actions);
    setShowForm(false);
    setText("");
    dispatchActionLogged(); // → useLoggedToday re-reads true
  }

  if (logged) {
    return <span className="text-xs font-mono text-ink-min">✓ ACTION LOGGED</span>;
  }

  if (!showForm) {
    return (
      <button
        onClick={() => setShowForm(true)}
        className="text-xs font-mono text-ink-lo hover:text-phos border border-white/[0.07] hover:border-signal-cyan/40
                   px-2 py-1 transition-colors"
      >
        LOG MY ACTION
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleLog();
          if (e.key === "Escape") setShowForm(false);
        }}
        placeholder="What did you do? (optional)"
        maxLength={120}
        className="text-xs bg-surface-base border border-white/15 text-ink-hi px-2 py-1 focus:outline-none focus:border-signal-cyan/40 transition-colors
                   placeholder:text-ink-min w-48"
      />
      <button
        onClick={handleLog}
        className="text-xs font-mono text-signal-cyan border border-white/15 px-2 py-1 hover:bg-signal-cyan/10 transition-colors"
      >
        LOG
      </button>
      <button
        onClick={() => setShowForm(false)}
        className="text-xs font-mono text-ink-min hover:text-phos transition-colors"
      >
        CANCEL
      </button>
    </div>
  );
}

// ── Widget ────────────────────────────────────────────────────────────────────

export default function CivicActionWidget() {
  const [open, setOpen] = useState(false);
  const [actions, setActions] = useState<CivicAction[]>([]);
  const [mounted, setMounted] = useState(false);

  const refresh = useCallback(() => {
    setActions(loadActions());
  }, []);

  useEffect(() => {
    // Client-only hydration gate: render nothing on the server (localStorage
    // isn't available there), then mount + subscribe to action-log updates.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
    refresh();
    window.addEventListener(TRACKER_EVENT, refresh);
    return () => window.removeEventListener(TRACKER_EVENT, refresh);
  }, [refresh]);

  if (!mounted) return null;

  const streak = computeStreak(actions);
  const total = actions.length;

  return (
    <div
      className="fixed bottom-4 right-4 z-50 flex flex-col items-end gap-2"
      role="complementary"
      aria-label="Civic action tracker"
    >
      {/* Expanded panel */}
      {open && (
        <div className="terminal-window w-72 max-h-96 flex flex-col shadow-xl shadow-black/50">
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.07]">
            <div>
              <span className="font-mono text-xs text-signal-cyan tracking-wider">
                MY CIVIC ACTIONS
              </span>
              {streak > 0 && (
                <div className="text-xs text-ink-min mt-0.5">
                  {streak} day streak · {total} total
                </div>
              )}
              {streak === 0 && total === 0 && (
                <div className="text-xs text-ink-min mt-0.5">
                  Log actions on issues you care about
                </div>
              )}
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close tracker"
              className="text-ink-min hover:text-phos text-xs transition-colors"
            >
              ✕
            </button>
          </div>

          <div className="overflow-y-auto flex-1 px-4 py-3">
            {actions.length === 0 ? (
              <p className="text-xs text-ink-min text-center py-4 leading-relaxed">
                No actions logged yet.{" "}
                <span className="text-ink-min">
                  Click &ldquo;Log my action&rdquo; on any issue.
                </span>
              </p>
            ) : (
              <ul className="space-y-2.5">
                {actions.slice(0, 20).map((a) => (
                  <li key={a.id} className="border-l-2 border-white/15 pl-3">
                    <p className="text-xs text-ink leading-snug">{a.action_text}</p>
                    <p className="text-xs text-ink-min mt-0.5 leading-snug line-clamp-1">
                      {a.issue_title}
                    </p>
                    <p className="text-xs text-ink-min">
                      {formatDate(a.date)}
                      {a.senator_name && ` · ${a.senator_name}`}
                    </p>
                  </li>
                ))}
                {actions.length > 20 && (
                  <li className="text-xs text-ink-min text-center pt-1">
                    +{actions.length - 20} more
                  </li>
                )}
              </ul>
            )}
          </div>

          {total > 0 && (
            <div className="px-4 py-2 border-t border-white/[0.07] flex justify-between items-center">
              <span className="text-xs text-ink-min">
                {streak > 1
                  ? `${streak}-day streak`
                  : total > 0
                    ? `${total} action${total !== 1 ? "s" : ""} taken`
                    : ""}
              </span>
              <button
                onClick={() => {
                  if (confirm("Clear all logged actions?")) {
                    saveActions([]);
                    refresh();
                  }
                }}
                className="text-xs text-ink-min hover:text-signal-red transition-colors"
              >
                CLEAR
              </button>
            </div>
          )}
        </div>
      )}

      {/* Toggle button */}
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={open ? "Close civic action tracker" : "Open civic action tracker"}
        className="terminal-window px-3 py-2 flex items-center gap-2 hover:border-signal-cyan/40 transition-colors"
      >
        <span className="text-xs font-mono text-signal-cyan">
          {open ? "✕ ACTIONS" : "MY ACTIONS"}
        </span>
        {total > 0 && !open && (
          <span className="text-xs font-mono text-ink-lo border border-white/[0.07] px-1">
            {total}
          </span>
        )}
        {streak > 1 && !open && (
          <span className="text-xs font-mono text-signal-amber">{streak}🔥</span>
        )}
      </button>
    </div>
  );
}
