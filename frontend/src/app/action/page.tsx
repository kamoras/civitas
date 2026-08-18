"use client";

import { Suspense, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import { fetchActionIssues, fetchOpenComments, OpenCommentItem } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useUserState } from "@/hooks/useUserState";
import { describeDaysLeft, formatUtcDate } from "@/lib/formatting";
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
import type { ActionIssue } from "@/types/action";
import { STATES } from "@/data/states";

const GlobeTab = dynamic(() => import("@/components/action/GlobeTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-ink-lo font-mono text-xs tracking-widest animate-pulse">
        LOADING GLOBE...
      </div>
    </div>
  ),
});

const ElectionsTab = dynamic(() => import("@/components/action/ElectionsTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-ink-lo font-mono text-xs tracking-widest animate-pulse">
        LOADING ELECTIONS...
      </div>
    </div>
  ),
});

const MonitorsTab = dynamic(() => import("./MonitorsTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-signal-amber font-mono text-xs tracking-widest animate-pulse">
        SCANNING NATIONAL CONCERNS...
      </div>
    </div>
  ),
});

const TimelineTab = dynamic(() => import("./TimelineTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-ind-purple font-mono text-xs tracking-widest animate-pulse">
        LOADING TIMELINE...
      </div>
    </div>
  ),
});

const MyRepsTab = dynamic(() => import("@/components/action/MyRepsTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-ink-lo font-mono text-xs tracking-widest animate-pulse">
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
        className="text-xs font-mono tracking-widest text-ink-lo hover:text-phos transition-colors"
        title="Change your state"
        aria-label="Change your state"
      >
        {userState} ✕
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="state-picker" className="text-xs font-mono tracking-widest text-ink-min">
        YOUR STATE
      </label>
      <select
        id="state-picker"
        value={userState || ""}
        onChange={(e) => onSelect(e.target.value || null)}
        autoComplete="address-level1"
        className="appearance-none bg-surface-base border border-white/15 text-ink-hi font-mono text-xs px-2 py-1 pr-6 cursor-pointer focus:outline-none focus:border-signal-cyan/40 transition-all"
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
                    "flex items-center gap-2 p-2 border border-white/15 bg-signal-cyan/10 hover:border-white/15 transition-colors text-sm";
                  const inner = (
                    <>
                      <span className="text-ink flex-1 truncate">
                        {trackActionText(action, internal)}
                      </span>
                      <span className="text-xs text-ink-lo shrink-0 ml-auto">
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
              <h4 className="font-mono text-xs tracking-widest text-ink-lo mb-2 uppercase">
                Official Legislation
              </h4>
              <div className="space-y-1.5">
                {issue.relatedBills.map((bill) => {
                  const { href, internal } = billLink(bill);
                  const linkClass =
                    "flex items-center gap-2 p-2 border border-signal-amber/40 bg-signal-amber/10 hover:border-signal-amber/40 transition-colors text-sm";
                  const inner = (
                    <>
                      <span className="text-xs font-mono tracking-wide text-ink-lo shrink-0">
                        {bill.id}
                      </span>
                      <span className="text-ink truncate">{bill.name}</span>
                      <span className="text-xs text-ink-lo shrink-0 ml-auto">
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
              <h4 className="font-mono text-xs tracking-widest text-ink-lo mb-2 uppercase">
                Officials in Coverage
              </h4>
              <div className="flex flex-wrap gap-2">
                {issue.relatedSenators.map((s) => (
                  <Link
                    key={s.id}
                    href={`/politicians/${s.id}`}
                    className={`flex items-start gap-1.5 px-2 py-1.5 border ${PARTY_BORDER[s.party]} bg-white/[0.03] hover:border-signal-cyan/40 transition-colors`}
                  >
                    <span className={`font-mono text-xs mt-0.5 shrink-0 ${PARTY_COLORS[s.party]}`}>
                      {s.party}
                    </span>
                    <div className="flex flex-col min-w-0">
                      <span className="text-sm text-ink leading-snug">{s.name}</span>
                      {s.matchReason && (
                        <span className="text-xs font-mono text-ink-min uppercase tracking-wide">
                          {s.matchReason}
                        </span>
                      )}
                    </div>
                    <span className="text-xs font-mono tracking-wide text-ink-lo mt-0.5 shrink-0">
                      {Math.round(s.overallScore)}
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}

          <SourceList
            issue={issue}
            className="flex items-center gap-2 flex-wrap pt-3 border-t border-white/[0.07]"
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
  // The selected day IS the request. Keying the fetch on it means the pager
  // can't get out of step with what is on screen: there is no separate
  // "which day did we last ask for" to drift from `selectedDate`.
  const [selectedDate, setSelectedDate] = useState<string | null>(initialDate || null);
  const request = useAsyncData(`action-issues:${selectedDate ?? "latest"}`, () =>
    fetchActionIssues(selectedDate || undefined)
  );
  const data = request.data;
  const loading = request.loading;
  const fetchError = request.error !== null;

  const availableDates = useMemo(() => data?.availableDates || [], [data?.availableDates]);
  const currentDate = selectedDate || data?.date || null;
  const currentIdx = currentDate ? availableDates.indexOf(currentDate) : 0;

  const goToPrev = useCallback(() => {
    if (currentIdx < availableDates.length - 1) {
      const d = availableDates[currentIdx + 1];
      setSelectedDate(d);
      onDateChange?.(d);
    }
  }, [currentIdx, availableDates, onDateChange]);

  const goToNext = useCallback(() => {
    if (currentIdx > 0) {
      const d = availableDates[currentIdx - 1];
      setSelectedDate(d);
      onDateChange?.(d);
    } else if (currentIdx === 0 && selectedDate) {
      setSelectedDate(null);
      onDateChange?.(null);
    }
  }, [currentIdx, availableDates, selectedDate, onDateChange]);

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
      <div className="panel max-w-md mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-ink-lo font-mono text-xs tracking-widest animate-pulse">
          SCANNING NEWS FEEDS...
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="panel max-w-lg mx-auto p-6 text-center" role="alert">
        <div className="text-signal-red font-mono text-sm tracking-widest mb-2">
          CONNECTION ERROR
        </div>
        <p className="text-ink-lo text-base mb-4">Could not load today&apos;s issues.</p>
        <button
          onClick={request.retry}
          className="text-signal-cyan font-mono text-xs tracking-widest border border-white/15 px-4 py-2 hover:bg-signal-cyan/10 transition-colors"
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
      <div className="panel max-w-lg mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-signal-amber font-mono text-sm tracking-widest mb-2">
          NO ISSUES YET
        </div>
        <p className="text-ink-lo text-base">Check back soon.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Date navigation */}
      {availableDates.length > 1 && (
        <div className="flex items-center justify-center gap-4 font-mono text-xs tracking-widest">
          <button
            onClick={goToPrev}
            disabled={currentIdx >= availableDates.length - 1}
            className="text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed transition-colors"
            aria-label="Previous day"
          >
            ← PREV
          </button>
          <span className="text-ink px-3 py-1 border border-white/[0.07] bg-white/[0.03] min-w-[110px] text-center">
            {currentDate
              ? formatUtcDate(currentDate, { month: "short", day: "numeric", year: "numeric" })
              : "—"}
          </span>
          <button
            onClick={goToNext}
            disabled={currentIdx <= 0 && !selectedDate}
            className="text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed transition-colors"
            aria-label="Next day"
          >
            NEXT →
          </button>
          {selectedDate && (
            <button
              onClick={() => {
                setSelectedDate(null);
                onDateChange?.(null);
              }}
              className="text-ink-lo hover:text-phos transition-colors ml-1"
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
          <span className="text-ink-min text-xs font-mono">
            Updated: {formatGeneratedAt(generatedAt)}
          </span>
        </div>
      )}

      {/* State selector bar */}
      <div className="flex items-center justify-between panel p-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono tracking-widest text-ink-min">PERSONALIZE</span>
          {userState && (
            <span className="text-xs font-mono text-signal-cyan border border-white/15 px-1.5 py-0.5 bg-signal-cyan/10">
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
          <h2 className="font-mono text-xs tracking-[0.3em] text-ink-min mb-3 px-1 uppercase">
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
  // The clock is read once, when the comment periods land, and carried
  // alongside them. Reading it again on every render would make the countdown
  // depend on when React happened to re-render — a value that changes without
  // any input changing is exactly what a render is not allowed to produce.
  const [loaded, setLoaded] = useState<{ items: OpenCommentItem[]; asOf: number } | null>(null);

  useEffect(() => {
    fetchOpenComments()
      .then((items) => setLoaded({ items, asOf: Date.now() }))
      .catch(() => {});
  }, []);

  if (!loaded || loaded.items.length === 0) return null;
  const items = loaded.items;
  const daysLeft = (closeDate: string) => describeDaysLeft(closeDate, loaded.asOf);

  return (
    <section aria-label="Open public comment periods" className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="w-1.5 h-1.5 bg-signal-amber/10 shrink-0" aria-hidden="true" />
        <span className="font-mono text-xs tracking-widest text-signal-amber">
          OPEN FOR PUBLIC COMMENT
        </span>
        <div className="flex-1 h-px bg-signal-amber/10" aria-hidden="true" />
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0 snap-x">
        {items.map((item) => (
          <div
            key={item.id}
            className="panel border border-signal-amber/40 bg-signal-amber/10 p-3 min-w-[220px] max-w-[260px] flex-shrink-0 snap-start flex flex-col gap-1.5"
          >
            <p className="text-xs text-ink leading-snug line-clamp-3 flex-1">{item.title}</p>
            {item.agencyName && (
              <div className="text-xs text-signal-amber font-mono tracking-wider truncate">
                {item.agencyName}
              </div>
            )}
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-signal-amber font-mono">
                {daysLeft(item.commentsCloseOn)}
              </span>
              <a
                href={item.commentUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-xs tracking-widest text-signal-amber border border-signal-amber/40 px-2 py-0.5 hover:bg-signal-amber/10 transition-colors shrink-0"
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

  // The address bar is the single source of truth for which tab is showing.
  // Tab clicks write ?tab= through the History API (see replaceUrl) and Next
  // feeds that back through useSearchParams, so the rendered tab follows the
  // URL with no second copy of the answer to keep in sync. (replaceState, so
  // tab switches deliberately do not stack up history entries.)
  const paramTab = searchParams.get("tab");
  const activeTab: Tab = isValidTab(paramTab) ? paramTab : "issues";
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

  const setActiveTab = useCallback(
    (tab: Tab) => {
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
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-4xl mx-auto relative z-10">
          <PageMasthead
            className="mb-6"
            eyebrow="Action Center · what is moving right now"
            title="Today on the record"
          >
            Issues surfaced from news and social coverage, the monitors tracking them, and the
            members of Congress who can act. Every item links back to its source.
          </PageMasthead>

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
