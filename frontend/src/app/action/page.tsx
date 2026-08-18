"use client";

import { Suspense, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import { fetchActionIssues, fetchOpenComments, OpenCommentItem } from "@/lib/api";
import { useUserState } from "@/hooks/useUserState";
import { formatUtcDate } from "@/lib/formatting";
import { PARTY_COLORS, PARTY_BORDER } from "@/lib/partyStyles";
import StancePulse from "@/components/action/StancePulse";
import { LogActionButton } from "@/components/action/CivicTracker";
import ShareButtons from "@/components/action/ShareButtons";
import BackToTop from "@/components/BackToTop";
import {
  PolicyBadge,
  MonitorChips,
  RepresentativeContacts,
  TrackLegislation,
  OfficialLegislation,
  RelatedDocuments,
  SourceList,
  billLink,
  trackActionLink,
  trackActionText,
  trackableActions,
} from "@/components/action/IssueEnrichment";

const CivicActionWidget = dynamic(() => import("@/components/action/CivicTracker"), { ssr: false });
import type { ActionIssue, ActionIssuesResponse } from "@/types/action";
import { STATES } from "@/data/states";

const GlobeTab = dynamic(() => import("@/components/action/GlobeTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-cyan/50 font-mono text-xs tracking-widest animate-pulse">
        LOADING GLOBE...
      </div>
    </div>
  ),
});

const ElectionsTab = dynamic(() => import("@/components/action/ElectionsTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-yellow/50 font-mono text-xs tracking-widest animate-pulse">
        LOADING ELECTIONS...
      </div>
    </div>
  ),
});

const MonitorsTab = dynamic(() => import("./MonitorsTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-amber-400/50 font-mono text-xs tracking-widest animate-pulse">
        SCANNING NATIONAL CONCERNS...
      </div>
    </div>
  ),
});

const TimelineTab = dynamic(() => import("./TimelineTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-purple-400/50 font-mono text-xs tracking-widest animate-pulse">
        LOADING TIMELINE...
      </div>
    </div>
  ),
});

const MyRepsTab = dynamic(() => import("@/components/action/MyRepsTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-pink/50 font-mono text-xs tracking-widest animate-pulse">
        LOADING REPRESENTATIVES...
      </div>
    </div>
  ),
});

type Tab = "issues" | "my-reps" | "monitors" | "timeline" | "elections" | "world";

function StatePicker({
  userState,
  onSelect,
  compact = false,
}: {
  userState: string | null;
  onSelect: (s: string | null) => void;
  compact?: boolean;
}) {
  if (compact && userState) {
    return (
      <button
        onClick={() => onSelect(null)}
        className="text-[10px] font-mono tracking-widest text-neon-cyan/60 hover:text-neon-cyan transition-colors"
        title="Change your state"
        aria-label="Change your state"
      >
        {userState} ✕
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label
        htmlFor="state-picker"
        className="text-[10px] font-mono tracking-widest text-matrix-green/40"
      >
        YOUR STATE
      </label>
      <select
        id="state-picker"
        value={userState || ""}
        onChange={(e) => onSelect(e.target.value || null)}
        autoComplete="address-level1"
        className="appearance-none bg-crt-black border border-matrix-green/25 text-matrix-green font-mono text-[11px] px-2 py-1 pr-6 cursor-pointer focus:outline-none focus:border-neon-cyan transition-all"
      >
        <option value="">SELECT</option>
        {STATES.map((s) => (
          <option key={s.code} value={s.code}>
            {s.code}
          </option>
        ))}
      </select>
    </div>
  );
}

/* One treatment for all six, not a different neon each.
   Six tabs in six accent colours made the tab bar the loudest element on the
   page and left the selected tab nowhere to go — every tab was already
   shouting. Selection is now carried by weight plus a solid phosphor rule,
   the same way the navbar marks the current page. */
const TABS: { id: Tab; label: string }[] = [
  { id: "issues", label: "ISSUES" },
  { id: "my-reps", label: "MY REPS" },
  { id: "monitors", label: "MONITORS" },
  { id: "timeline", label: "TIMELINE" },
  { id: "elections", label: "ELECTIONS" },
  { id: "world", label: "GLOBE" },
];

function HeroIssue({
  issue,
  userState,
  onNavigate,
  isDeepLinked = false,
}: {
  issue: ActionIssue;
  userState: string | null;
  onNavigate?: (tab: Tab) => void;
  isDeepLinked?: boolean;
}) {
  const heroRef = useRef<HTMLDivElement>(null);
  const today = new Date().toISOString().slice(0, 10);
  const onMonitorSelect = onNavigate ? () => onNavigate("monitors") : undefined;

  useEffect(() => {
    if (isDeepLinked && heroRef.current) {
      heroRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [isDeepLinked]);

  return (
    <article ref={heroRef} className="border border-phos/20 bg-surface p-6 sm:p-8">
      <div className="mb-4 flex flex-wrap items-center gap-3 font-mono text-xs">
        <span className="border border-phos/40 px-2 py-0.5 tracking-[0.14em] text-phos-mid">
          TOP ISSUE
        </span>
        <span className="text-ink-lo">{issue.date}</span>
        <span className="text-ink-min" aria-hidden="true">
          ·
        </span>
        <span className="text-ink-min">ISSUE-{issue.id}</span>
      </div>

      <h2 className="mb-4 font-display text-2xl font-bold leading-tight text-ink-hi sm:text-[28px]">
        {issue.title}
      </h2>

      <p className="mb-6 max-w-3xl font-display text-base leading-relaxed text-ink sm:text-[17px]">
        {issue.summary}
      </p>

      {issue.policyAreas.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap mb-6">
          {issue.policyAreas.map((area) => (
            <PolicyBadge key={area} area={area} />
          ))}
        </div>
      )}

      <MonitorChips slugs={issue.relatedMonitorSlugs} onSelect={onMonitorSelect} />

      <RepresentativeContacts issue={issue} userState={userState} />

      {issue.facts.length > 0 && (
        <div className="mb-6">
          <h3 className="mb-3 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
            Key facts
          </h3>
          <ol className="space-y-2">
            {issue.facts.map((fact, i) => (
              <li key={i} className="flex gap-3">
                <span className="mt-0.5 shrink-0 font-mono text-xs text-phos-mid">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="font-display text-[15px] leading-relaxed text-ink">{fact}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Specific actions only — representative contact handled above */}
      <TrackLegislation issue={issue} />

      <OfficialLegislation issue={issue} />

      <RelatedDocuments issue={issue} today={today} />

      <SourceList issue={issue} />

      <StancePulse
        issueId={issue.id}
        initialConcerned={issue.concernedCount || 0}
        initialNotPriority={issue.notPriorityCount || 0}
      />
      <div className="mt-3 flex items-center justify-between gap-3">
        <a
          href={`/issue/${issue.id}`}
          className="border border-phos/40 px-3 py-1.5 font-mono text-xs uppercase tracking-[0.12em] text-phos-mid transition-colors hover:border-phos hover:text-phos"
        >
          Read full story →
        </a>
        <LogActionButton issueTitle={issue.title} />
      </div>

      <ShareButtons issue={issue} />
    </article>
  );
}

function SecondaryIssue({
  issue,
  userState,
  onNavigate,
  deepLinked = false,
  onToggle,
}: {
  issue: ActionIssue;
  userState: string | null;
  onNavigate?: (tab: Tab) => void;
  deepLinked?: boolean;
  onToggle?: (id: number, expanded: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(deepLinked);
  const cardRef = useRef<HTMLDivElement>(null);
  const onMonitorSelect = onNavigate ? () => onNavigate("monitors") : undefined;

  // If this issue is deep-linked, expand and scroll to it once data is ready
  useEffect(() => {
    if (deepLinked) {
      setExpanded(true);
      // Delay slightly so the panel renders before scrolling
      const t = setTimeout(() => {
        cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
      return () => clearTimeout(t);
    }
  }, [deepLinked]);

  function handleToggle() {
    const next = !expanded;
    setExpanded(next);
    onToggle?.(issue.id, next);
  }

  return (
    <article ref={cardRef} className="border border-white/[0.09] bg-surface">
      <button
        onClick={handleToggle}
        className="flex w-full items-start justify-between gap-4 p-4 text-left sm:p-5"
        aria-expanded={expanded}
        aria-controls={`issue-detail-${issue.id}`}
      >
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2 font-mono text-xs">
            <span className="text-ink-min">
              {issue.date} · ISSUE-{issue.id}
            </span>
            {issue.policyAreas.map((area) => (
              <PolicyBadge key={area} area={area} />
            ))}
          </div>
          <h3 className="font-display text-lg font-semibold leading-snug text-ink-hi">
            {issue.title}
          </h3>
          {!expanded && (
            <p className="mt-1 line-clamp-2 font-display text-[15px] leading-relaxed text-ink-lo">
              {issue.summary}
            </p>
          )}
        </div>
        <span
          className="mt-0.5 shrink-0 font-mono text-lg leading-none text-ink-min"
          aria-hidden="true"
        >
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded && (
        <div
          id={`issue-detail-${issue.id}`}
          className="space-y-4 border-t border-white/[0.07] px-4 pb-4 pt-4 sm:px-5 sm:pb-5"
        >
          <p className="font-display text-[15px] leading-relaxed text-ink">{issue.summary}</p>

          <MonitorChips slugs={issue.relatedMonitorSlugs} onSelect={onMonitorSelect} />

          {issue.facts.length > 0 && (
            <div>
              <h4 className="mb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                Key facts
              </h4>
              <ol className="space-y-1.5">
                {issue.facts.map((fact, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-0.5 shrink-0 font-mono text-xs text-phos-mid">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="font-display text-[15px] leading-relaxed text-ink">
                      {fact}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          <RepresentativeContacts issue={issue} userState={userState} />

          {trackableActions(issue).length > 0 && (
            <div>
              <h4 className="mb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                Track legislation
              </h4>
              <div className="space-y-1.5">
                {trackableActions(issue).map((action, i) => {
                  const { href, internal } = trackActionLink(issue, action);
                  const linkClass =
                    "flex items-center gap-2 p-2 border border-neon-cyan/15 bg-neon-cyan/5 hover:border-neon-cyan/30 transition-colors text-sm";
                  const inner = (
                    <>
                      <span className="text-matrix-green/70 flex-1 truncate">
                        {trackActionText(action, internal)}
                      </span>
                      <span className="text-[10px] text-neon-cyan/40 shrink-0 ml-auto">
                        {internal ? "→" : "↗"}
                      </span>
                    </>
                  );
                  return internal ? (
                    <Link key={i} href={href} className={linkClass}>
                      {inner}
                    </Link>
                  ) : (
                    <a
                      key={i}
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={linkClass}
                    >
                      {inner}
                    </a>
                  );
                })}
              </div>
            </div>
          )}

          {issue.relatedBills && issue.relatedBills.length > 0 && (
            <div>
              <h4 className="font-mono text-[10px] tracking-widest text-neon-yellow/50 mb-2 uppercase">
                Official Legislation
              </h4>
              <div className="space-y-1.5">
                {issue.relatedBills.map((bill) => {
                  const { href, internal } = billLink(bill);
                  const linkClass =
                    "flex items-center gap-2 p-2 border border-neon-yellow/15 bg-neon-yellow/5 hover:border-neon-yellow/30 transition-colors text-sm";
                  const inner = (
                    <>
                      <span className="text-[10px] font-mono tracking-wide text-neon-yellow/60 shrink-0">
                        {bill.id}
                      </span>
                      <span className="text-matrix-green/70 truncate">{bill.name}</span>
                      <span className="text-[10px] text-neon-cyan/40 shrink-0 ml-auto">
                        {internal ? "→" : "↗"}
                      </span>
                    </>
                  );
                  return internal ? (
                    <Link key={bill.id} href={href} className={linkClass}>
                      {inner}
                    </Link>
                  ) : (
                    <a
                      key={bill.id}
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={linkClass}
                    >
                      {inner}
                    </a>
                  );
                })}
              </div>
            </div>
          )}

          {issue.relatedSenators && issue.relatedSenators.length > 0 && (
            <div>
              <h4 className="font-mono text-[10px] tracking-widest text-neon-pink/50 mb-2 uppercase">
                Officials in Coverage
              </h4>
              <div className="flex flex-wrap gap-2">
                {issue.relatedSenators.map((s) => (
                  <Link
                    key={s.id}
                    href={`/politicians/${s.id}`}
                    className={`flex items-start gap-1.5 px-2 py-1.5 border ${PARTY_BORDER[s.party]} bg-matrix-dark-green/20 hover:border-neon-cyan/40 transition-colors`}
                  >
                    <span
                      className={`font-mono text-[10px] mt-0.5 shrink-0 ${PARTY_COLORS[s.party]}`}
                    >
                      {s.party}
                    </span>
                    <div className="flex flex-col min-w-0">
                      <span className="text-sm text-matrix-green/70 leading-snug">{s.name}</span>
                      {s.matchReason && (
                        <span className="text-[10px] font-mono text-matrix-green/35 uppercase tracking-wide">
                          {s.matchReason}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] font-mono tracking-wide text-neon-cyan/50 mt-0.5 shrink-0">
                      {Math.round(s.overallScore)}
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}

          <SourceList
            issue={issue}
            className="flex items-center gap-2 flex-wrap pt-3 border-t border-matrix-green/10"
          />

          <StancePulse
            issueId={issue.id}
            initialConcerned={issue.concernedCount || 0}
            initialNotPriority={issue.notPriorityCount || 0}
          />
          <div className="mt-3 flex justify-end">
            <LogActionButton issueTitle={issue.title} />
          </div>

          <ShareButtons issue={issue} />
        </div>
      )}
    </article>
  );
}

function IssuesTab({
  userState,
  setUserState,
  onNavigate,
  initialDate,
  onDateChange,
  initialIssueId,
  onIssueChange,
}: {
  userState: string | null;
  setUserState: (s: string | null) => void;
  onNavigate?: (tab: Tab) => void;
  initialDate?: string | null;
  onDateChange?: (date: string | null) => void;
  initialIssueId?: number | null;
  onIssueChange?: (id: number | null) => void;
}) {
  const [data, setData] = useState<ActionIssuesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(initialDate || null);

  const loadIssues = useCallback((date?: string) => {
    setLoading(true);
    setFetchError(false);
    fetchActionIssues(date)
      .then((d) => setData(d))
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadIssues(initialDate || undefined);
  }, [loadIssues, initialDate]);

  const availableDates = useMemo(() => data?.availableDates || [], [data?.availableDates]);
  const currentDate = selectedDate || data?.date || null;
  const currentIdx = currentDate ? availableDates.indexOf(currentDate) : 0;

  const goToPrev = useCallback(() => {
    if (currentIdx < availableDates.length - 1) {
      const d = availableDates[currentIdx + 1];
      setSelectedDate(d);
      loadIssues(d);
      onDateChange?.(d);
    }
  }, [currentIdx, availableDates, loadIssues, onDateChange]);

  const goToNext = useCallback(() => {
    if (currentIdx > 0) {
      const d = availableDates[currentIdx - 1];
      setSelectedDate(d);
      loadIssues(d);
      onDateChange?.(d);
    } else if (currentIdx === 0 && selectedDate) {
      setSelectedDate(null);
      loadIssues();
      onDateChange?.(null);
    }
  }, [currentIdx, availableDates, selectedDate, loadIssues, onDateChange]);

  const generatedAt = data?.generatedAt;

  function formatGeneratedAt(iso: string): string {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }

  if (loading) {
    return (
      <div
        className="terminal-window max-w-md mx-auto p-6 text-center"
        role="status"
        aria-live="polite"
      >
        <div className="text-neon-cyan/50 font-mono text-xs tracking-widest animate-pulse">
          SCANNING NEWS FEEDS...
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-6 text-center" role="alert">
        <div className="text-red-400 font-mono text-sm tracking-widest mb-2">CONNECTION ERROR</div>
        <p className="text-matrix-green/50 text-sm mb-4">Could not load today&apos;s issues.</p>
        <button
          onClick={() => loadIssues(selectedDate || undefined)}
          className="text-neon-cyan font-mono text-xs tracking-widest border border-neon-cyan/30 px-4 py-2 hover:bg-neon-cyan/10 transition-colors"
        >
          RETRY
        </button>
      </div>
    );
  }

  const heroIssue = data?.issues?.[0];
  const secondaryIssues = data?.issues?.slice(1) || [];

  if (!heroIssue) {
    return (
      <div
        className="terminal-window max-w-lg mx-auto p-6 text-center"
        role="status"
        aria-live="polite"
      >
        <div className="text-neon-yellow font-mono text-sm tracking-widest mb-2">NO ISSUES YET</div>
        <p className="text-matrix-green/50 text-sm">Check back soon.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Date navigation */}
      {availableDates.length > 1 && (
        <div className="flex items-center justify-center gap-4 font-mono text-[11px] tracking-widest">
          <button
            onClick={goToPrev}
            disabled={currentIdx >= availableDates.length - 1}
            className="text-matrix-green/50 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed transition-colors"
            aria-label="Previous day"
          >
            ← PREV
          </button>
          <span className="text-matrix-green/70 px-3 py-1 border border-matrix-green/15 bg-matrix-green/5 min-w-[110px] text-center">
            {currentDate
              ? formatUtcDate(currentDate, { month: "short", day: "numeric", year: "numeric" })
              : "—"}
          </span>
          <button
            onClick={goToNext}
            disabled={currentIdx <= 0 && !selectedDate}
            className="text-matrix-green/50 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed transition-colors"
            aria-label="Next day"
          >
            NEXT →
          </button>
          {selectedDate && (
            <button
              onClick={() => {
                setSelectedDate(null);
                loadIssues();
                onDateChange?.(null);
              }}
              className="text-neon-cyan/50 hover:text-neon-cyan transition-colors ml-1"
              aria-label="Jump to present"
            >
              LATEST
            </button>
          )}
        </div>
      )}

      {/* Data freshness timestamp */}
      {generatedAt && (
        <div className="text-center">
          <span className="text-matrix-green/30 text-[10px] font-mono">
            Updated: {formatGeneratedAt(generatedAt)}
          </span>
        </div>
      )}

      {/* State selector bar */}
      <div className="flex items-center justify-between terminal-window p-3">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono tracking-widest text-matrix-green/35">
            PERSONALIZE
          </span>
          {userState && (
            <span className="text-[10px] font-mono text-neon-cyan/70 border border-neon-cyan/20 px-1.5 py-0.5 bg-neon-cyan/5">
              {STATES.find((s) => s.code === userState)?.name || userState} — links personalized
            </span>
          )}
        </div>
        {!userState ? (
          <StatePicker userState={userState} onSelect={setUserState} />
        ) : (
          <StatePicker userState={userState} onSelect={setUserState} compact />
        )}
      </div>

      <HeroIssue
        issue={heroIssue}
        userState={userState}
        onNavigate={onNavigate}
        isDeepLinked={initialIssueId === heroIssue.id}
      />

      {secondaryIssues.length > 0 && (
        <div>
          <h2 className="font-mono text-[10px] tracking-[0.3em] text-matrix-green/40 mb-3 px-1 uppercase">
            More Issues to Watch
          </h2>
          <div className="space-y-3">
            {secondaryIssues.map((issue) => (
              <SecondaryIssue
                key={issue.id}
                issue={issue}
                userState={userState}
                onNavigate={onNavigate}
                deepLinked={initialIssueId === issue.id}
                onToggle={(id, expanded) => onIssueChange?.(expanded ? id : null)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const VALID_TABS = new Set<string>([
  "issues",
  "my-reps",
  "monitors",
  "timeline",
  "elections",
  "world",
]);
function isValidTab(s: string | null): s is Tab {
  return s !== null && VALID_TABS.has(s);
}

function OpenCommentsBanner() {
  const [items, setItems] = useState<OpenCommentItem[]>([]);

  useEffect(() => {
    fetchOpenComments()
      .then(setItems)
      .catch(() => {});
  }, []);

  if (items.length === 0) return null;

  function daysLeft(closeDate: string): string {
    const diff = Math.ceil((new Date(closeDate).getTime() - Date.now()) / 86400000);
    return diff <= 0 ? "closes today" : diff === 1 ? "1 day left" : `${diff} days left`;
  }

  return (
    <section aria-label="Open public comment periods" className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400/80 shrink-0" aria-hidden="true" />
        <span className="font-mono text-[10px] tracking-widest text-amber-400/70">
          OPEN FOR PUBLIC COMMENT
        </span>
        <div className="flex-1 h-px bg-amber-400/15" aria-hidden="true" />
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0 snap-x">
        {items.map((item) => (
          <div
            key={item.id}
            className="terminal-window border border-amber-400/30 bg-amber-400/5 p-3 min-w-[220px] max-w-[260px] flex-shrink-0 snap-start flex flex-col gap-1.5"
          >
            <p className="text-[11px] text-matrix-green/80 leading-snug line-clamp-3 flex-1">
              {item.title}
            </p>
            {item.agencyName && (
              <div className="text-[9px] text-amber-400/40 font-mono tracking-wider truncate">
                {item.agencyName}
              </div>
            )}
            <div className="flex items-center justify-between gap-2">
              <span className="text-[9px] text-amber-400/60 font-mono">
                {daysLeft(item.commentsCloseOn)}
              </span>
              <a
                href={item.commentUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[9px] tracking-widest text-amber-400/70 border border-amber-400/25 px-2 py-0.5 hover:bg-amber-400/10 transition-colors shrink-0"
              >
                COMMENT ↗
              </a>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function ActionPage() {
  return (
    <Suspense>
      <ActionPageInner />
    </Suspense>
  );
}

function ActionPageInner() {
  const searchParams = useSearchParams();

  // Push view state (which tab, which day, which issue) into the address bar.
  //
  // Deliberately NOT router.replace(): /action is statically prerendered, and
  // in a production build Next's client router treats a same-route navigation
  // as already-satisfied once the page was loaded with a query string. The
  // address bar then stays frozen on whatever ?tab= it was opened with — every
  // later tab click swapped the panel but left the URL reading ?tab=timeline,
  // and even the navbar's own /action link couldn't clear it. Only reproduces
  // in `next build`, never in `next dev`.
  //
  // The History API is Next's supported path for search-param-only updates and
  // keeps usePathname/useSearchParams in sync without a navigation.
  const replaceUrl = useCallback((url: string) => {
    window.history.replaceState(null, "", url);
  }, []);

  const paramTab = searchParams.get("tab");
  const [activeTab, setActiveTabRaw] = useState<Tab>(isValidTab(paramTab) ? paramTab : "issues");
  const [userState, setUserState] = useUserState();
  const [sharedIssues, setSharedIssues] = useState<ActionIssue[]>([]);

  useEffect(() => {
    fetchActionIssues()
      .then((d) => setSharedIssues(d.issues))
      .catch(() => {
        /* silently ignore — IssuesTab has its own error handling */
      });
  }, []);

  // ?date= and ?issue=<id> are read once, from the URL the page was opened
  // with, and deliberately not re-read afterwards. The page writes those same
  // params back as the user pages through days and expands cards, and Next
  // feeds a history.replaceState straight back through useSearchParams — so
  // re-reading them would make IssuesTab treat the user's own click as a fresh
  // arrival: SecondaryIssue would smooth-scroll the card out from under them,
  // and the day pager would reload the day it just loaded.
  const [deepLink] = useState(() => {
    const rawIssue = searchParams.get("issue");
    return {
      date: searchParams.get("date"),
      issue: rawIssue ? parseInt(rawIssue, 10) || null : null,
    };
  });

  useEffect(() => {
    const t = searchParams.get("tab");
    if (isValidTab(t) && t !== activeTab) {
      setActiveTabRaw(t);
    } else if (!t && activeTab !== "issues") {
      setActiveTabRaw("issues");
    }
    // activeTab intentionally omitted: including it would re-trigger the effect
    // on every user-initiated tab switch, creating a loop with setActiveTab.
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  const setActiveTab = useCallback(
    (tab: Tab) => {
      setActiveTabRaw(tab);
      const url = tab === "issues" ? "/action" : `/action?tab=${tab}`;
      replaceUrl(url);
      // Focus the newly selected *tab*, not its panel. The tabs use a roving
      // tabindex, so the incoming tab has to be focused explicitly or the
      // keyboard user is stranded on an element that just became tabindex=-1.
      // Focusing the panel instead moved focus out of the tablist entirely,
      // which meant the Arrow/Home/End handler below stopped receiving keys —
      // one arrow press worked and every one after it did nothing. The panel
      // stays tabbable (tabIndex=0), so Tab still reaches the content next.
      requestAnimationFrame(() => {
        document.getElementById(`tab-${tab}`)?.focus();
      });
    },
    [replaceUrl]
  );

  // Update URL when a secondary issue is expanded/collapsed
  const handleIssueChange = useCallback(
    (id: number | null) => {
      const url = id ? `/action?issue=${id}` : "/action";
      replaceUrl(url);
    },
    [replaceUrl]
  );

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-4xl mx-auto relative z-10">
          <header className="mb-6 border-b-3 border-phos pb-5">
            <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
              Action Center · what is moving right now
            </p>
            <h1 className="mt-3 font-display text-3xl font-extrabold uppercase leading-none tracking-[-0.02em] text-ink-hi sm:text-4xl">
              Today on the record
            </h1>
            <p className="mt-3 max-w-2xl font-display text-base leading-relaxed text-ink-lo">
              Issues surfaced from news and social coverage, the monitors tracking them, and the
              members of Congress who can act. Every item links back to its source.
            </p>
          </header>

          {/* Open comment periods banner */}
          <OpenCommentsBanner />

          {/* Tab bar */}
          <div
            role="tablist"
            aria-label="Action Center sections"
            className="sticky top-[82px] z-30 -mx-4 mb-8 flex gap-0 overflow-x-auto border-b border-white/15 bg-surface-base/95 px-4 backdrop-blur-sm sm:mx-0 sm:px-0"
            onKeyDown={(e) => {
              const tabs = TABS.map((t) => t.id);
              const idx = tabs.indexOf(activeTab);
              if (e.key === "ArrowRight") {
                e.preventDefault();
                setActiveTab(tabs[(idx + 1) % tabs.length]);
              } else if (e.key === "ArrowLeft") {
                e.preventDefault();
                setActiveTab(tabs[(idx - 1 + tabs.length) % tabs.length]);
              } else if (e.key === "Home") {
                e.preventDefault();
                setActiveTab(tabs[0]);
              } else if (e.key === "End") {
                e.preventDefault();
                setActiveTab(tabs[tabs.length - 1]);
              }
            }}
          >
            {TABS.map((tab) => (
              <button
                key={tab.id}
                role="tab"
                id={`tab-${tab.id}`}
                aria-selected={activeTab === tab.id}
                aria-controls={`tabpanel-${tab.id}`}
                tabIndex={activeTab === tab.id ? 0 : -1}
                onClick={() => setActiveTab(tab.id)}
                className={`-mb-px whitespace-nowrap border-b-3 px-3 py-3 font-mono text-xs uppercase tracking-[0.14em] transition-colors sm:px-5 ${
                  activeTab === tab.id
                    ? "border-phos text-ink-hi"
                    : "border-transparent text-ink-min hover:text-ink-lo"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab panels */}
          <div
            role="tabpanel"
            id={`tabpanel-${activeTab}`}
            aria-labelledby={`tab-${activeTab}`}
            tabIndex={0}
          >
            {activeTab === "issues" && (
              <IssuesTab
                userState={userState}
                setUserState={setUserState}
                onNavigate={setActiveTab}
                initialDate={deepLink.date}
                onDateChange={(d) => {
                  const url = d ? `/action?date=${d}` : "/action";
                  replaceUrl(url);
                }}
                initialIssueId={deepLink.issue}
                onIssueChange={handleIssueChange}
              />
            )}
            {activeTab === "my-reps" && (
              <MyRepsTab userState={userState} setUserState={setUserState} issues={sharedIssues} />
            )}
            {activeTab === "monitors" && <MonitorsTab />}
            {activeTab === "timeline" && <TimelineTab />}
            {activeTab === "elections" && <ElectionsTab />}
            {activeTab === "world" && <GlobeTab />}
          </div>
        </div>
      </main>
      <Footer />
      <BackToTop />
      <CivicActionWidget />
    </>
  );
}
