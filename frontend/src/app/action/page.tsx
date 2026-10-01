"use client";

import { Suspense, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageFallback from "@/components/layout/PageFallback";
import PageMasthead from "@/components/layout/PageMasthead";
import { fetchActionIssues, fetchOpenComments, OpenCommentItem } from "@/lib/api";
import { useAsyncData, type AsyncData } from "@/hooks/useAsyncData";
import { commentPeriodToday, describeDaysLeft, formatUtcDate } from "@/lib/formatting";
import ShareButtons from "@/components/action/ShareButtons";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { focusTabWhenSelected, keepFocusOnSelectedTab, retryKeepingFocus } from "@/lib/tabFocus";
import BackToTop from "@/components/BackToTop";
import {
  Coverage,
  DevelopingDisclosure,
  IssueImage,
  IssueMeta,
  IssueTags,
  SECTION_HEADING,
  SourceList,
  SummarySource,
  TEXT_LINK,
  WhatYouCanDo,
} from "@/components/action/IssueEnrichment";
import type { ActionIssue, ActionIssuesResponse } from "@/types/action";
import { countIsOfficial } from "@/lib/developing";
import { tabControl } from "@/lib/controlStyles";
import { issuesUrl } from "@/lib/routes";

/** Plain status line, the loading state the records pages use: no pulse. */
function Status({ children }: { children: React.ReactNode }) {
  return (
    <p
      role="status"
      aria-live="polite"
      className="py-10 font-mono text-sm tracking-[0.12em] text-ink-min"
    >
      {children}
    </p>
  );
}

/** A load failure, with a retry when the caller has one. */
function LoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-baseline justify-between gap-3 border-l-2 border-signal-red bg-surface px-4 py-3 font-mono text-sm text-signal-red"
    >
      <span>{message}</span>
      {onRetry && (
        <button onClick={retryKeepingFocus(onRetry)} className={TEXT_LINK}>
          Try again
        </button>
      )}
    </div>
  );
}

const MonitorsTab = dynamic(() => import("./MonitorsTab"), {
  loading: () => <Status>Reading the national monitors…</Status>,
});

const TimelineTab = dynamic(() => import("./TimelineTab"), {
  loading: () => <Status>Reading the archive…</Status>,
});

/* The tab ids are what ?tab= carries, and they predate the labels: links out
   in the wild (ACTION_CENTER_HREF, ACTION_CENTER_MONITORS_HREF, the bill
   page's "In the Action Center" box) name them, so the labels changed and
   the ids did not. */
type Tab = "issues" | "monitors" | "timeline";

/* One treatment for all three, carried by weight plus a solid ink rule, the
   same way the navbar marks the current page. */
const TABS: { id: Tab; label: string }[] = [
  { id: "issues", label: "Today" },
  { id: "monitors", label: "Ongoing" },
  { id: "timeline", label: "Archive" },
];

function isValidTab(s: string | null): s is Tab {
  return s !== null && TABS.some((t) => t.id === s);
}

/**
 * Everything below an issue's title: the same content, in the same order, for
 * the top issue and for an expanded secondary one.
 */
function IssueBody({
  issue,
  today,
  headingLevel,
  onMonitor,
}: {
  issue: ActionIssue;
  today: string;
  headingLevel: "h3" | "h4";
  onMonitor: (slug: string) => void;
}) {
  return (
    <>
      <p className="mb-4 max-w-3xl font-display text-base leading-relaxed text-ink sm:text-[17px]">
        {issue.summary}
        <SummarySource issue={issue} />
      </p>
      {issue.status === "developing" && (
        <DevelopingDisclosure
          sourceType={issue.sourceType}
          countOfficial={countIsOfficial(issue)}
        />
      )}
      <IssueTags issue={issue} onMonitor={onMonitor} />

      <WhatYouCanDo issue={issue} today={today} headingLevel={headingLevel} />
      <Coverage issue={issue} headingLevel={headingLevel} />

      <div className="mt-8 flex flex-col gap-3 border-t border-white/[0.07] pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
          <SourceList issue={issue} />
          <a href={`/issue/${issue.publicId}`} className={TEXT_LINK}>
            Read full story →
          </a>
        </div>
        <ShareButtons issue={issue} />
      </div>
    </>
  );
}

function HeroIssue({
  issue,
  today,
  isDeepLinked = false,
  onMonitor,
}: {
  issue: ActionIssue;
  today: string;
  isDeepLinked?: boolean;
  onMonitor: (slug: string) => void;
}) {
  const heroRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (isDeepLinked && heroRef.current) {
      heroRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [isDeepLinked]);

  return (
    <article
      ref={heroRef}
      {...{ [SHARE_SECTION_ATTR]: `issue-${issue.publicId}` }}
      className="scroll-mt-[calc(var(--header-clearance)+3rem)] border border-white/25 border-t-3 bg-surface p-5 sm:p-8"
    >
      <IssueMeta issue={issue} lead="Top issue" />
      <h2 className="mb-4 mt-3 text-balance font-display text-2xl font-bold leading-tight text-ink-hi sm:text-[28px]">
        {issue.title}
      </h2>
      <IssueImage issue={issue} />
      <IssueBody issue={issue} today={today} headingLevel="h3" onMonitor={onMonitor} />
    </article>
  );
}

function SecondaryIssue({
  issue,
  today,
  deepLinked = false,
  onToggle,
  onMonitor,
}: {
  issue: ActionIssue;
  today: string;
  deepLinked?: boolean;
  onToggle?: (id: string, expanded: boolean) => void;
  onMonitor: (slug: string) => void;
}) {
  const [expanded, setExpanded] = useState(deepLinked);
  const cardRef = useRef<HTMLElement>(null);

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
    <li className="border-b border-white/[0.07]">
      <article
        ref={cardRef}
        {...{ [SHARE_SECTION_ATTR]: `issue-${issue.publicId}` }}
        className="scroll-mt-[calc(var(--header-clearance)+3rem)]"
      >
        <h3>
          <button
            onClick={handleToggle}
            className="flex w-full items-start gap-4 py-4 text-left"
            aria-expanded={expanded}
            aria-controls={`issue-detail-${issue.id}`}
          >
            {!expanded && <IssueImage issue={issue} size="thumbnail" />}
            <span className="min-w-0 flex-1">
              <IssueMeta issue={issue} as="span" />
              {(issue.policyAreas ?? []).length > 0 && (
                <span className="mt-1 block font-mono text-xs tracking-[0.08em] text-ink-lo">
                  {issue.policyAreas.join(" · ")}
                </span>
              )}
              <span className="mt-1.5 block font-display text-lg font-semibold leading-snug text-ink-hi">
                {issue.title}
              </span>
              {!expanded && (
                <span className="mt-1 line-clamp-2 block font-display text-[15px] leading-relaxed text-ink-lo">
                  {issue.summary}
                </span>
              )}
            </span>
            <span
              className="mt-0.5 shrink-0 font-mono text-lg leading-none text-ink-min"
              aria-hidden="true"
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            >
              {expanded ? "−" : "+"}
            </span>
          </button>
        </h3>

        {expanded && (
          <div id={`issue-detail-${issue.id}`} className="pb-6">
            <IssueImage issue={issue} />
            <IssueBody issue={issue} today={today} headingLevel="h4" onMonitor={onMonitor} />
          </div>
        )}
      </article>
    </li>
  );
}

function OpenComments() {
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

  return (
    <section aria-labelledby="open-comments-heading" className="mt-12">
      <h2 id="open-comments-heading" className={SECTION_HEADING}>
        <span>Open for public comment</span>
        <Link
          href="/explore"
          className="normal-case tracking-[0.08em] text-ink-lo hover:text-ink-hi"
        >
          Search in Explore →
        </Link>
      </h2>
      <ul>
        {loaded.items.map((item) => (
          <li
            key={item.id}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 border-b border-white/[0.07] py-3"
          >
            <span className="min-w-0">
              <span className="block font-mono text-xs uppercase tracking-[0.08em] text-ink-min">
                {[item.agencyName, item.docType?.replace(/_/g, " ")].filter(Boolean).join(" · ")}
              </span>
              <Link
                href={`/explore/${item.id}`}
                className="mt-0.5 block font-display text-[15px] leading-snug text-ink-hi hover:underline"
              >
                {item.title}
              </Link>
            </span>
            <span className="text-right">
              <span className="block whitespace-nowrap font-mono text-xs tracking-[0.06em] text-signal-amber">
                {describeDaysLeft(item.commentsCloseOn, loaded.asOf)}
              </span>
              <Link
                href={`/explore/${item.id}#comment`}
                className={`${TEXT_LINK} mt-1 inline-block`}
              >
                Comment →
              </Link>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function IssuesTab({
  request,
  selectedDate,
  initialIssueId,
  onIssueChange,
  onMonitor,
}: {
  request: AsyncData<ActionIssuesResponse>;
  selectedDate: string | null;
  initialIssueId?: string | null;
  onIssueChange?: (id: string | null) => void;
  onMonitor: (slug: string) => void;
}) {
  // Read once per render of a loaded day, and only from the clock's date in the
  // comment-deadline zone — the same rule the server applies on the issue page.
  const today = commentPeriodToday();

  if (request.loading) return <Status>Reading today&apos;s issues…</Status>;
  if (request.error !== null) {
    return <LoadError message="Could not load today's issues." onRetry={request.retry} />;
  }

  const issues = request.data?.issues ?? [];
  const heroIssue = issues[0];
  const secondaryIssues = issues.slice(1);

  return (
    <div>
      {heroIssue ? (
        <HeroIssue
          issue={heroIssue}
          today={today}
          isDeepLinked={initialIssueId === heroIssue.publicId}
          onMonitor={onMonitor}
        />
      ) : (
        <p className="border-l-2 border-ink-min/60 py-2 pl-4 font-display text-base text-ink-lo">
          {selectedDate
            ? // Issues are kept for 14 days unless they were posted
              // (action_center._cleanup_old_unposted_issues); the Archive
              // keeps every day's top issue.
              "Nothing from this day is still on the Action Center. Its top issue is kept in the Archive."
            : "No issues on the record for this day yet. The Action Center refreshes hourly."}
        </p>
      )}

      {secondaryIssues.length > 0 && (
        <section aria-labelledby="more-issues-heading" className="mt-12">
          <h2 id="more-issues-heading" className={SECTION_HEADING}>
            <span>
              {/* A developing seat flip is listed beside the newest day
                  whatever its own date (backend _latest_current_issues). */}
              {secondaryIssues.every((i) => i.date === heroIssue?.date)
                ? "More issues this day"
                : "More issues"}
            </span>
            <span aria-hidden="true">{secondaryIssues.length}</span>
          </h2>
          <ul>
            {secondaryIssues.map((issue) => (
              <SecondaryIssue
                key={issue.id}
                issue={issue}
                today={today}
                deepLinked={initialIssueId === issue.publicId}
                onToggle={(id, expanded) => onIssueChange?.(expanded ? id : null)}
                onMonitor={onMonitor}
              />
            ))}
          </ul>
        </section>
      )}

      <OpenComments />
    </div>
  );
}

/** Previous/next day and the refresh time, in the masthead's aside slot. */
function DayPager({
  data,
  selectedDate,
  onSelect,
}: {
  data: ActionIssuesResponse | null;
  selectedDate: string | null;
  onSelect: (d: string | null) => void;
}) {
  // Newest first. The first entry is the live view (no ?date=).
  const availableDates = useMemo(() => data?.availableDates ?? [], [data?.availableDates]);
  const currentDate = selectedDate || data?.date || null;
  // By comparison, not by index: a day outside the list (an old ?date= link,
  // a day whose issues were cleaned up) still has real neighbours on it.
  const prev = currentDate ? availableDates.find((d) => d < currentDate) : undefined;
  // The live view has no "next": it is the newest thing there is. (Its date
  // can trail availableDates[0] while nothing is marked current; see
  // _latest_current_issues.) "Back to the latest" returns to it.
  const next =
    selectedDate && currentDate
      ? [...availableDates].reverse().find((d) => d > currentDate)
      : undefined;

  const short = (d: string) => formatUtcDate(d, { month: "short", day: "numeric" });

  const generatedAt = data?.generatedAt;
  let updated = "";
  if (generatedAt && !selectedDate) {
    const d = new Date(generatedAt);
    if (!Number.isNaN(d.getTime())) {
      updated = d.toLocaleString(undefined, { hour: "numeric", minute: "2-digit" });
    }
  }

  const link = "text-ink-lo hover:text-ink-hi disabled:cursor-not-allowed disabled:text-ink-min/50";

  return (
    <nav
      aria-label="Day"
      className="grid justify-items-end gap-1.5 font-mono text-xs tracking-[0.12em]"
    >
      <div className="flex items-baseline gap-4">
        <button
          onClick={() => prev && onSelect(prev)}
          disabled={!prev}
          className={link}
          aria-label={prev ? `Previous day, ${short(prev)}` : "No earlier day"}
        >
          ← {prev ? short(prev) : "Earlier"}
        </button>
        <span className="uppercase text-ink-hi">
          {currentDate
            ? formatUtcDate(currentDate, { month: "short", day: "numeric", year: "numeric" })
            : "—"}
        </span>
        <button
          onClick={() => next && onSelect(next)}
          disabled={!next}
          className={link}
          aria-label={next ? `Next day, ${short(next)}` : "No later day"}
        >
          {next ? short(next) : "Later"} →
        </button>
      </div>
      {updated && <span className="tracking-[0.04em] text-ink-min">Updated {updated}</span>}
      {selectedDate && (
        <button onClick={() => onSelect(null)} className={`${link} tracking-[0.04em]`}>
          Back to the latest
        </button>
      )}
    </nav>
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
  const replaceUrl = useCallback((url: string) => {
    window.history.replaceState(null, "", url);
  }, []);

  // Switching tabs is a destination, so it gets a history entry and Back
  // returns to the tab you came from. Expanding a card or paging a day stays
  // on replaceState above: those refine what you are already looking at, and
  // pushing them would make Back walk through every card someone opened
  // before it left the page.
  const pushUrl = useCallback((url: string) => {
    window.history.pushState(null, "", url);
  }, []);

  // The address bar is the single source of truth for which tab is showing.
  // Tab clicks write ?tab= through the History API and Next feeds that back
  // through useSearchParams, so the rendered tab follows the URL — Back and
  // Forward included — with no second copy of the answer to keep in sync.
  const paramTab = searchParams.get("tab");
  const activeTab: Tab = isValidTab(paramTab) ? paramTab : "issues";
  // Back and Forward change the tab with focus still on the tab bar.
  useEffect(() => keepFocusOnSelectedTab(`tab-${activeTab}`), [activeTab]);

  // ?issue= and ?monitor= describe how the page was opened: an item to
  // expand and scroll to on arrival. They are read once, from the URL the page
  // was opened with, and not re-read: the page writes ?issue= back as cards
  // are expanded, and Next feeds a history.replaceState straight back through
  // useSearchParams, so re-reading it would treat the user's own click as a
  // fresh arrival and smooth-scroll the card out from under them.
  const [deepLink] = useState(() => ({
    issue: searchParams.get("issue"),
    monitor: searchParams.get("monitor"),
  }));

  // Arrivals are one-shot. Any ordinary tab switch or day change clears them,
  // or every return to a tab would re-open the item, scroll to it and pull
  // focus off the tab bar, killing arrow-key navigation.
  const [issueLink, setIssueLink] = useState<string | null>(deepLink.issue);
  const [monitorSlug, setMonitorSlug] = useState<string | null>(deepLink.monitor);

  // The day, by contrast, is view state, and the address bar is its source on
  // Today, like the tab: Back and Forward land on the day the URL names. Away
  // from Today, `todayDate` keeps the day Today was last showing, so switching
  // back returns to it (and says so in the URL).
  //
  // The selected day IS the request. Keying the fetch on it means the pager
  // can't get out of step with what is on screen.
  const urlDate = searchParams.get("date");
  const [todayDate, setTodayDate] = useState<string | null>(urlDate);
  // While Today is showing it follows the URL, however the URL got there
  // (a click, Back, Forward). Adjusted during render, React's pattern for
  // state derived from a changing input, so no render shows the two apart.
  if (activeTab === "issues" && todayDate !== urlDate) setTodayDate(urlDate);
  const selectedDate = activeTab === "issues" ? urlDate : todayDate;
  const request = useAsyncData(`action-issues:${selectedDate ?? "latest"}`, () =>
    fetchActionIssues(selectedDate || undefined)
  );

  const selectDate = useCallback(
    (d: string | null) => {
      setIssueLink(null);
      replaceUrl(issuesUrl(d, null));
    },
    [replaceUrl]
  );

  const setActiveTab = useCallback(
    (tab: Tab) => {
      // Re-selecting the showing tab is not a navigation: no history entry,
      // and nothing it shows is reset.
      if (tab !== activeTab) {
        setMonitorSlug(null);
        setIssueLink(null);
        pushUrl(tab === "issues" ? issuesUrl(todayDate, null) : `/action?tab=${tab}`);
      }
      // The panel stays tabbable (tabIndex=0), so Tab still reaches content.
      focusTabWhenSelected(`tab-${tab}`);
    },
    [pushUrl, activeTab, todayDate]
  );

  // Opening a monitor or a day from inside the page. Both are links out in the
  // wild (?tab=monitors&monitor=, ?date=), but a <Link> to this same route is
  // a soft navigation that the latched deep link above never sees, so the
  // in-page versions set the state directly and write the URL themselves.
  const openMonitor = useCallback(
    (slug: string) => {
      setMonitorSlug(slug);
      setIssueLink(null);
      pushUrl(`/action?tab=monitors&monitor=${encodeURIComponent(slug)}`);
    },
    [pushUrl]
  );
  const openDay = useCallback(
    (d: string) => {
      setTodayDate(d);
      setIssueLink(null);
      pushUrl(issuesUrl(d, null));
      window.scrollTo({ top: 0 });
      focusTabWhenSelected("tab-issues");
    },
    [pushUrl]
  );

  // Update URL when a secondary issue is expanded/collapsed
  const handleIssueChange = useCallback(
    (id: string | null) => {
      replaceUrl(issuesUrl(selectedDate, id));
    },
    [replaceUrl, selectedDate]
  );

  return (
    <>
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-4xl mx-auto relative z-10">
          <PageMasthead
            eyebrow="Action Center · what is moving right now"
            title="Today on the record"
            aside={
              activeTab === "issues" ? (
                <DayPager data={request.data} selectedDate={selectedDate} onSelect={selectDate} />
              ) : undefined
            }
          >
            Issues surfaced from news and social coverage, the ongoing concerns they belong to, and
            the members of Congress who can act. Every item links back to its source.
          </PageMasthead>

          {/* Tab bar.

              `overflow-y-hidden` is load-bearing, not tidying: `overflow-x-auto`
              also makes overflow-y `auto`, and the tabs measure 42.67px against
              a 42px content box, so that fractional pixel is enough to raise a
              vertical scrollbar. globals.css paints every scrollbar thumb in
              full-strength phosphor, so it rendered as a bright green 4px bar
              parked beside the tab row, reading as a deliberate accent. */}
          <div
            role="tablist"
            aria-label="Action Center sections"
            className="sticky top-[82px] z-30 -mx-4 mb-8 mt-6 flex gap-0 overflow-x-auto overflow-y-hidden border-b border-white/15 bg-surface-base/95 px-4 backdrop-blur-sm sm:mx-0 sm:px-0"
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
                // Only the selected tab's panel is rendered, so only it can
                // name one; the others would point at an id that isn't there.
                aria-controls={activeTab === tab.id ? `tabpanel-${tab.id}` : undefined}
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
                request={request}
                selectedDate={selectedDate}
                initialIssueId={issueLink}
                onIssueChange={handleIssueChange}
                onMonitor={openMonitor}
              />
            )}
            {activeTab === "monitors" && (
              <MonitorsTab key={monitorSlug ?? ""} initialSlug={monitorSlug} />
            )}
            {activeTab === "timeline" && (
              <TimelineTab
                onOpenDay={openDay}
                onOpenMonitor={openMonitor}
                openableDates={request.data ? (request.data.availableDates ?? []) : null}
              />
            )}
          </div>
        </div>
      </main>
      <Footer />
      <BackToTop />
    </>
  );
}
