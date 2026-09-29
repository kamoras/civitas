"use client";

import { Suspense, useEffect, useState, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageFallback from "@/components/layout/PageFallback";
import PageMasthead from "@/components/layout/PageMasthead";
import { fetchActionIssues, fetchOpenComments, OpenCommentItem } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useUserState } from "@/hooks/useUserState";
import {
  commentPeriodToday,
  describeDaysLeft,
  formatUtcDate,
  isNewFact,
  issueDateLabel,
  issueRef,
} from "@/lib/formatting";
import { PARTY_COLORS, PARTY_BORDER } from "@/lib/partyStyles";
import StancePulse from "@/components/action/StancePulse";
import { LogActionButton } from "@/components/action/CivicTracker";
import ShareButtons from "@/components/action/ShareButtons";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { focusTabWhenSelected } from "@/lib/tabFocus";
import BackToTop from "@/components/BackToTop";
import {
  PolicyBadge,
  TrendingBadge,
  DevelopingBadge,
  DevelopingDisclosure,
  NewFactTag,
  MonitorChips,
  RepresentativeContacts,
  FollowResults,
  TrackLegislation,
  OfficialLegislation,
  RelatedDocuments,
  SourceList,
  IssueImage,
  billLink,
  trackActionLink,
  trackActionText,
  trackableActions,
} from "@/components/action/IssueEnrichment";
import { countIsOfficial, factsHeading } from "@/lib/developing";

const CivicActionWidget = dynamic(() => import("@/components/action/CivicTracker"), { ssr: false });
import type { ActionIssue } from "@/types/action";
import { STATES } from "@/data/states";
import { tabControl } from "@/lib/controlStyles";
import { ACTION_CENTER_HREF } from "@/lib/routes";

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
  const today = commentPeriodToday();
  const onMonitorSelect = onNavigate ? () => onNavigate("monitors") : undefined;

  useEffect(() => {
    if (isDeepLinked && heroRef.current) {
      heroRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [isDeepLinked]);

  return (
    <article
      ref={heroRef}
      {...{ [SHARE_SECTION_ATTR]: `issue-${issue.publicId}` }}
      className="border border-phos/20 bg-surface p-6 sm:p-8"
    >
      <div className="mb-4 flex flex-wrap items-center gap-3 font-mono text-xs">
        <span className="border border-phos/40 px-2 py-0.5 tracking-[0.14em] text-phos-mid">
          TOP ISSUE
        </span>
        {issue.status === "developing" && <DevelopingBadge />}
        {issue.isTrending && <TrendingBadge />}
        <span className="text-ink-lo">{issueDateLabel(issue)}</span>
        <span className="text-ink-min" aria-hidden="true">
          ·
        </span>
        <span className="text-ink-min">{issueRef(issue.publicId)}</span>
      </div>

      <h2 className="mb-4 font-display text-2xl font-bold leading-tight text-ink-hi sm:text-[28px]">
        {issue.title}
      </h2>

      <IssueImage issue={issue} />

      <p className="mb-6 max-w-3xl font-display text-base leading-relaxed text-ink sm:text-[17px]">
        {issue.summary}
      </p>

      {issue.status === "developing" && (
        <DevelopingDisclosure
          sourceType={issue.sourceType}
          countOfficial={countIsOfficial(issue)}
        />
      )}

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
            {factsHeading(issue)}
          </h3>
          <ol className="space-y-2">
            {issue.facts.map((fact, i) => (
              <li key={i} className="flex gap-3">
                <span className="mt-0.5 shrink-0 font-mono text-xs text-phos-mid">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="font-display text-[15px] leading-relaxed text-ink">
                  {fact}
                  {issue.factSources?.[i] && (
                    <span className="ml-2 font-mono text-xs text-ink-min">
                      {issue.factSources[i]}
                    </span>
                  )}
                  {isNewFact(issue.newFacts, fact) && <NewFactTag />}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Specific actions only — representative contact handled above */}
      <FollowResults issue={issue} />
      <TrackLegislation issue={issue} />

      <OfficialLegislation issue={issue} />

      <RelatedDocuments issue={issue} today={today} />

      <SourceList issue={issue} />

      {/* Controls, not content: left out of a shared image of the card. */}
      <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
        <StancePulse
          issueId={issue.id}
          initialConcerned={issue.concernedCount || 0}
          initialNotPriority={issue.notPriorityCount || 0}
        />
      </div>
      <div
        className="mt-3 flex items-center justify-between gap-3"
        {...{ [SHARE_EXCLUDE_ATTR]: "" }}
      >
        <a
          href={`/issue/${issue.publicId}`}
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
  onToggle?: (id: string, expanded: boolean) => void;
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
    onToggle?.(issue.publicId, next);
  }

  return (
    <article
      ref={cardRef}
      {...{ [SHARE_SECTION_ATTR]: `issue-${issue.publicId}` }}
      className="border border-white/[0.09] bg-surface"
    >
      <button
        onClick={handleToggle}
        className="flex w-full items-start justify-between gap-4 p-4 text-left sm:p-5"
        aria-expanded={expanded}
        aria-controls={`issue-detail-${issue.id}`}
      >
        <IssueImage issue={issue} size="thumbnail" />
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2 font-mono text-xs">
            <span className="text-ink-min">
              {issueDateLabel(issue)} · {issueRef(issue.publicId)}
            </span>
            {issue.status === "developing" && <DevelopingBadge />}
            {issue.isTrending && <TrendingBadge />}
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
          {expanded && issue.status === "developing" && (
            <DevelopingDisclosure
              sourceType={issue.sourceType}
              countOfficial={countIsOfficial(issue)}
            />
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
                {factsHeading(issue)}
              </h4>
              <ol className="space-y-1.5">
                {issue.facts.map((fact, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-0.5 shrink-0 font-mono text-xs text-phos-mid">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="font-display text-[15px] leading-relaxed text-ink">
                      {fact}
                      {issue.factSources?.[i] && (
                        <span className="ml-2 font-mono text-xs text-ink-min">
                          {issue.factSources[i]}
                        </span>
                      )}
                      {isNewFact(issue.newFacts, fact) && <NewFactTag />}
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

          {/* Controls, not content: left out of a shared image of the card. */}
          <div {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
            <StancePulse
              issueId={issue.id}
              initialConcerned={issue.concernedCount || 0}
              initialNotPriority={issue.notPriorityCount || 0}
            />
          </div>
          <div className="mt-3 flex justify-end" {...{ [SHARE_EXCLUDE_ATTR]: "" }}>
            <LogActionButton issueTitle={issue.title} />
          </div>

          <ShareButtons issue={issue} />
        </div>
      )}
    </article>
  );
}

/** A `?date=` that names a real calendar day (YYYY-MM-DD), or null: a
 * malformed or impossible one ("2026-1-2", "2026-02-30") would otherwise be
 * requested as a day and labelled "Invalid Date" or a day that isn't it. */
function isoDayOrNull(value: string | null): string | null {
  // Year 0000 too: JS Date accepts it, the API's date.fromisoformat doesn't.
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return null;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value ? value : null;
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
  initialIssueId?: string | null;
  onIssueChange?: (id: string | null) => void;
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

  // The last day list stays while the next day loads, so the pager (and
  // the keyboard focus on it) stays put through a page turn instead of
  // being unmounted by the loading panel.
  const [lastDates, setLastDates] = useState<string[]>([]);
  const freshDates = data?.availableDates;
  if (freshDates && freshDates !== lastDates) setLastDates(freshDates);
  const availableDates = freshDates ?? lastDates;
  const currentDate = selectedDate || data?.date || null;
  const currentIdx = currentDate ? availableDates.indexOf(currentDate) : 0;

  // An issue link (?issue=) scrolls to its card once, on arrival. Turning
  // the page ends the arrival: coming back to that day must not expand and
  // scroll to the card again.
  const [issueArrival, setIssueArrival] = useState<string | null>(initialIssueId ?? null);
  const goTo = useCallback(
    (d: string | null) => {
      setIssueArrival(null);
      setSelectedDate(d);
      onDateChange?.(d);
    },
    [onDateChange]
  );

  // While a day loads the list is the previous day's, which (14 newest plus
  // that day's neighbours) may not hold the true next day, and on a cold
  // deep link is empty: the pager waits for the day it is on.
  const canPrev = !loading && currentIdx >= 0 && currentIdx < availableDates.length - 1;
  const canNext = !loading && (currentIdx > 0 || !!selectedDate);
  const goToPrev = useCallback(() => {
    if (canPrev) goTo(availableDates[currentIdx + 1]);
  }, [canPrev, availableDates, currentIdx, goTo]);
  const goToNext = useCallback(() => {
    if (!canNext) return;
    // The newest day, or a chosen day the list doesn't hold: on to the
    // latest.
    goTo(currentIdx > 0 ? availableDates[currentIdx - 1] : null);
  }, [canNext, availableDates, currentIdx, goTo]);
  const goToLatest = useCallback(() => {
    if (selectedDate) goTo(null);
  }, [selectedDate, goTo]);

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

  // Shown on an empty day too: a day whose issues all moved on (a
  // re-matched issue is restamped to the day that matched it) is reached
  // by the timeline's links, and without a pager it strands the reader.
  const pager = (availableDates.length > 1 || selectedDate) && (
    <div className="flex items-center justify-center gap-4 font-mono text-xs tracking-widest">
      {/* aria-disabled, not disabled: a button that turns disabled while
          focused (paging to the oldest or newest day) drops focus to the
          page body. The handlers do nothing at either end. */}
      <button
        onClick={goToPrev}
        aria-disabled={!canPrev}
        className="text-ink-lo hover:text-phos aria-disabled:text-ink-min aria-disabled:cursor-not-allowed transition-colors"
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
        aria-disabled={!canNext}
        className="text-ink-lo hover:text-phos aria-disabled:text-ink-min aria-disabled:cursor-not-allowed transition-colors"
        aria-label="Next day"
      >
        NEXT →
      </button>
      {/* Kept mounted (aria-disabled on the latest day) so activating it
          doesn't unmount the focused button. */}
      <button
        onClick={goToLatest}
        aria-disabled={!selectedDate}
        className="text-ink-lo hover:text-phos aria-disabled:text-ink-min aria-disabled:cursor-not-allowed transition-colors ml-1"
        aria-label="Jump to present"
      >
        LATEST
      </button>
    </div>
  );

  // Every state below renders the pager first in the same wrapper, so
  // React keeps the same pager element (and its focused button) across
  // loading, error, empty and loaded.
  if (loading) {
    return (
      <div className="space-y-6">
        {pager}
        <div className="panel max-w-md mx-auto p-6 text-center" role="status" aria-live="polite">
          <div className="text-ink-lo font-mono text-xs tracking-widest animate-pulse">
            SCANNING NEWS FEEDS...
          </div>
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="space-y-6">
        {pager}
        <div className="panel max-w-lg mx-auto p-6 text-center" role="alert">
          <div className="text-signal-red font-mono text-sm tracking-widest mb-2">
            CONNECTION ERROR
          </div>
          <p className="text-ink-lo text-base mb-4">Could not load these issues.</p>
          <button
            onClick={request.retry}
            className="text-signal-cyan font-mono text-xs tracking-widest border border-white/15 px-4 py-2 hover:bg-signal-cyan/10 transition-colors"
          >
            RETRY
          </button>
        </div>
      </div>
    );
  }

  const heroIssue = data?.issues?.[0];
  const secondaryIssues = data?.issues?.slice(1) || [];

  if (!heroIssue) {
    return (
      <div className="space-y-6">
        {pager}
        <div className="panel max-w-lg mx-auto p-6 text-center" role="status" aria-live="polite">
          {selectedDate ? (
            <p className="text-ink-lo text-base">No issues are recorded for this day.</p>
          ) : (
            <>
              <div className="text-signal-amber font-mono text-sm tracking-widest mb-2">
                NO ISSUES YET
              </div>
              <p className="text-ink-lo text-base">Check back soon.</p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {pager}

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
        isDeepLinked={issueArrival === heroIssue.publicId}
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
                deepLinked={issueArrival === issue.publicId}
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

/** A search string in one canonical spelling, so a URL the page wrote and the
 *  same URL read back through useSearchParams compare equal. */
function searchKey(params: URLSearchParams): string {
  const sorted = new URLSearchParams(params);
  sorted.sort();
  return sorted.toString();
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
    <Suspense
      fallback={
        <PageFallback
          eyebrow={"Action Center · what is moving right now"}
          title={"Today on the record"}
          rows={4}
        />
      }
    >
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
  //
  // Every URL the page writes itself is noted in `ownWrites` first, so the
  // arrival logic below can tell the page's own writes (which must not
  // re-fire an arrival) from a navigation the user made to a different day
  // or issue (which must).
  //
  // State, not a ref: it is read during render below. Noted before the
  // History call, so the note is committed no later than the router update
  // Next dispatches for it (a transition, which renders after it).
  const [ownWrites, setOwnWrites] = useState<string[]>([]);
  const noteOwnWrite = useCallback((url: string) => {
    const key = searchKey(new URL(url, window.location.href).searchParams);
    // Rewriting the URL already showing changes no search params, so nothing
    // would ever come back to match (and clear) the note.
    if (key === searchKey(new URLSearchParams(window.location.search))) return;
    setOwnWrites((w) => [...w, key]);
  }, []);
  const replaceUrl = useCallback(
    (url: string) => {
      noteOwnWrite(url);
      window.history.replaceState(null, "", url);
    },
    [noteOwnWrite]
  );

  // Switching tabs is a destination, so it gets a history entry and Back
  // returns to the tab you came from. Expanding a card or paging a day stays
  // on replaceState above: those refine what you are already looking at, and
  // pushing them would make Back walk through every card someone opened
  // before it left the page.
  const pushUrl = useCallback(
    (url: string) => {
      noteOwnWrite(url);
      window.history.pushState(null, "", url);
    },
    [noteOwnWrite]
  );

  // The address bar is the single source of truth for which tab is showing.
  // Tab clicks write ?tab= through the History API and Next feeds that back
  // through useSearchParams, so the rendered tab follows the URL — Back and
  // Forward included — with no second copy of the answer to keep in sync.
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
  //
  // That latch is per *arrival*, not per mount, though. An in-app <Link> to
  // this same route (a Timeline entry's /action?date=…, My Reps'
  // /action?issue=…, the navbar's ACTION_CENTER_HREF) is a soft navigation:
  // the page never remounts, so a mount-only latch kept the day the page was
  // opened on and showed today's issues under a ?date= URL — only under
  // `next build`; a cold load was fine. So a search the page did not write
  // itself (a <Link>, Back/Forward) is a new arrival: it is latched afresh and
  // IssuesTab remounts on it (`seq`), while the page's own writes, matched
  // against `ownWrites`, change nothing.
  const currentSearch = searchKey(searchParams);
  const [deepLink, setDeepLink] = useState(() => ({
    date: isoDayOrNull(searchParams.get("date")),
    issue: searchParams.get("issue"),
    seq: 0,
    search: currentSearch,
  }));
  // Adjusted during render, not in an effect, so IssuesTab never mounts
  // once more on the previous arrival (a stale fetch, a stale scroll)
  // before the new one lands.
  if (currentSearch !== deepLink.search) {
    const own = ownWrites.indexOf(currentSearch);
    if (own !== -1) {
      // Next may report rapid writes one by one; anything written before
      // this one has been superseded either way.
      setOwnWrites(ownWrites.slice(own + 1));
      setDeepLink({ ...deepLink, search: currentSearch });
    } else {
      setDeepLink({
        date: isoDayOrNull(searchParams.get("date")),
        issue: searchParams.get("issue"),
        seq: deepLink.seq + 1,
        search: currentSearch,
      });
    }
  }

  // Back/Forward swaps the tab under a keyboard user whose focus is still on
  // the tab they left, now tabindex=-1 (roving tabindex): move it to the tab
  // that is showing. Only from inside the tablist — focus elsewhere is not
  // ours to take.
  const tablistRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const focused = document.activeElement;
    const incoming = document.getElementById(`tab-${activeTab}`);
    if (
      incoming &&
      focused !== incoming &&
      focused instanceof HTMLElement &&
      tablistRef.current?.contains(focused)
    ) {
      incoming.focus();
    }
  }, [activeTab]);

  const setActiveTab = useCallback(
    (tab: Tab) => {
      // Every tab names itself, issues included (ACTION_CENTER_HREF): a bare
      // /action entry is what Next's router cache later answers an in-app
      // <Link href="/action"> with, restoring whatever search it last saw.
      const url = tab === "issues" ? ACTION_CENTER_HREF : `/action?tab=${tab}`;
      if (tab !== activeTab) {
        pushUrl(url);
        // Leaving for another tab ends the arrival: coming back to ISSUES
        // (whose URL names no day or issue) shows the latest day, not the
        // day the page was opened on, and doesn't re-scroll to the card it
        // was opened at. A still-mounted card can only see its deepLinked
        // prop go false here, which its arrival effect ignores.
        setDeepLink((d) => ({ ...d, date: null, issue: null }));
      }
      // The panel stays tabbable (tabIndex=0), so Tab still reaches content.
      focusTabWhenSelected(`tab-${tab}`);
    },
    [pushUrl, activeTab]
  );

  // Update URL when a secondary issue is expanded/collapsed
  const handleIssueChange = useCallback(
    (id: string | null) => {
      const url = id ? `/action?issue=${id}` : ACTION_CENTER_HREF;
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

          {/* Tab bar.

              `overflow-y-hidden` is load-bearing, not tidying: `overflow-x-auto`
              also makes overflow-y `auto`, and the tabs measure 42.67px against
              a 42px content box, so that fractional pixel is enough to raise a
              vertical scrollbar. globals.css paints every scrollbar thumb in
              full-strength phosphor, so it rendered as a bright green 4px bar
              parked beside the tab row, reading as a deliberate accent. */}
          <div
            ref={tablistRef}
            role="tablist"
            aria-label="Action Center sections"
            className="sticky top-[82px] z-30 -mx-4 mb-8 flex gap-0 overflow-x-auto overflow-y-hidden border-b border-white/15 bg-surface-base/95 px-4 backdrop-blur-sm sm:mx-0 sm:px-0"
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
                className={`-mb-px whitespace-nowrap border-b-3 px-3 py-3 font-mono text-xs uppercase tracking-[0.14em] transition-colors sm:px-5 ${tabControl(
                  activeTab === tab.id
                )}`}
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
                key={deepLink.seq}
                userState={userState}
                setUserState={setUserState}
                onNavigate={setActiveTab}
                initialDate={deepLink.date}
                onDateChange={(d) => {
                  const url = d ? `/action?date=${d}` : ACTION_CENTER_HREF;
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
