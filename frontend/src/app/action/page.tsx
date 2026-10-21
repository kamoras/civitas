"use client";

import RelatedVotes from "@/components/action/RelatedVotes";
import { Suspense, useEffect, useMemo, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageFallback from "@/components/layout/PageFallback";
import PageMasthead from "@/components/layout/PageMasthead";
import { fetchActionIssues, fetchOpenComments, OpenCommentItem } from "@/lib/api";
import { useAsyncData, type AsyncData } from "@/hooks/useAsyncData";
import {
  commentPeriodToday,
  formatUtcDate,
  issueRef,
  policyAreaLabel,
  restatesTitle,
} from "@/lib/formatting";
import ShareButtons from "@/components/action/ShareButtons";
import ContactYourMembers from "@/components/action/ContactYourMembers";
import TrackedLink from "@/components/action/TrackedLink";
import { focusTabWhenSelected, keepFocusOnSelectedTab, retryKeepingFocus } from "@/lib/tabFocus";
import BackToTop from "@/components/BackToTop";
import {
  billLink,
  Coverage,
  DevelopingDisclosure,
  followResultsActions,
  IssueCommands,
  IssueImage,
  OutletLink,
  SourceList,
  TAP_TARGET,
  TEXT_LINK,
  WhatYouCanDo,
} from "@/components/action/IssueEnrichment";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox, { ACTION, CHIP } from "@/components/shared/RecordBox";
import type { ActionIssue, ActionIssuesResponse } from "@/types/action";
import { countIsOfficial } from "@/lib/developing";
import { CHAMBER_NAME, rollCallHref } from "@/lib/congress";
import { tabControl } from "@/lib/controlStyles";
import { OPEN_COMMENTS_HREF, issuesUrl } from "@/lib/routes";
import AgencyLink from "@/components/agencies/AgencyLink";
import { exploreHref } from "@/lib/exploreHref";
import { scrollBehavior } from "@/lib/motion";

/** Plain status line, the loading state the records pages use: no pulse. */
function Status({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" aria-live="polite" className="py-10 font-mono text-sm text-ink-min">
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

/** "Oct 19", or "Oct 19, updated Oct 20" for a story re-matched to fresh
 *  coverage (the day it appears under is `date`; it surfaced earlier). */
function shortDated(issue: ActionIssue): string {
  const short = (d: string) => formatUtcDate(d, { month: "short", day: "numeric" });
  return issue.firstSurfaced && issue.firstSurfaced !== issue.date
    ? `${short(issue.firstSurfaced)}, updated ${short(issue.date)}`
    : short(issue.date);
}

/**
 * One issue as a record box (AGENTS.md, "Boxes, not blocks"): the outlet's
 * own headline, its quoted line credited to the outlet, the policy area and
 * the day, the ongoing story it belongs to, the bills and votes it links,
 * and its commands as buttons. Everything else the issue has (its picture,
 * every row of "What you can do", the related votes with their tallies, the
 * quoted coverage, the sources, sharing) is behind its "More". The top
 * issue spans the grid and carries the reader's own members' panel.
 */
function IssueBox({
  issue,
  today,
  lead = false,
  deepLinked = false,
  onToggle,
  onMonitor,
}: {
  issue: ActionIssue;
  today: string;
  lead?: boolean;
  deepLinked?: boolean;
  onToggle?: (id: string, expanded: boolean) => void;
  onMonitor: (slug: string) => void;
}) {
  const id = `issue-${issue.publicId}`;

  // A deep link opens the box's "More" (below) and scrolls it into view
  // once the grid has rendered.
  useEffect(() => {
    if (!deepLinked) return;
    const t = setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    }, 100);
    return () => clearTimeout(t);
  }, [deepLinked, id]);

  const developing = issue.status === "developing";
  const kicker = [
    lead ? "Top issue" : null,
    ...(issue.policyAreas ?? []).map(policyAreaLabel),
    shortDated(issue),
    developing ? "Developing" : null,
    issue.isTrending ? "Trending" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const quoted = !!issue.summary && !restatesTitle(issue.title, issue.summary);
  const outlets = issue.sourceNames ?? [];
  const line = quoted
    ? {
        quote: {
          text: issue.summary,
          source: issue.summarySource ? (
            <OutletLink name={issue.summarySource} url={issue.summarySourceUrl} />
          ) : (
            outlets.join(" · ")
          ),
        },
      }
    : { dek: outlets.length > 0 ? `Reported by ${outlets.join(", ")}` : undefined };
  const monitors = issue.relatedMonitors ?? [];
  const bills = issue.relatedBills ?? [];
  const votes = issue.relatedVotes ?? [];
  const ownMembers = lead && followResultsActions(issue).length === 0;

  return (
    <RecordBox
      id={id}
      share={id}
      lead={lead}
      compact={!lead}
      kicker={kicker}
      mark={lead ? "!" : undefined}
      tone={developing ? "deadline" : lead ? "record" : "neutral"}
      title={issue.title}
      href={`/issue/${issue.publicId}`}
      {...line}
      below={
        <div className="grid gap-3">
          {developing && (
            <DevelopingDisclosure
              sourceType={issue.sourceType}
              countOfficial={countIsOfficial(issue)}
            />
          )}
          {(monitors.length > 0 || bills.length > 0 || votes.length > 0) && (
            <div className="flex flex-wrap gap-2">
              {monitors.map(({ slug, title }) => (
                // In place: a <Link> to this same route is a soft navigation
                // the page never re-reads (it latches ?monitor= at mount).
                <button key={slug} onClick={() => onMonitor(slug)} className={CHIP}>
                  Ongoing: {title}
                </button>
              ))}
              {bills.map((bill) => {
                const { href, internal } = billLink(bill);
                return (
                  <TrackedLink
                    key={bill.id}
                    issuePublicId={issue.publicId}
                    kind="bill"
                    href={href}
                    internal={internal}
                    className={CHIP}
                    ariaLabel={`${bill.id}: ${bill.name}`}
                  >
                    {bill.id}
                  </TrackedLink>
                );
              })}
              {votes.map((v) => (
                <Link
                  key={`${v.chamber}-${v.congress}-${v.session}-${v.number}`}
                  href={rollCallHref(v)}
                  className={CHIP}
                >
                  {CHAMBER_NAME[v.chamber]} vote {v.number}
                </Link>
              ))}
            </div>
          )}
          <IssueCommands issue={issue} today={today} className={ACTION} />
          {ownMembers && (
            <ContactYourMembers
              issue={issue}
              afterNamed={(issue.relatedSenators ?? []).length > 0}
            />
          )}
        </div>
      }
      more={{
        label: "every action, the coverage and sources",
        open: deepLinked,
        onToggle: onToggle && ((open) => onToggle(issue.publicId, open)),
        children: (
          <>
            <IssueImage issue={issue} />
            <WhatYouCanDo
              issue={issue}
              today={today}
              headingLevel="h4"
              ownMembers={!ownMembers}
              className="mt-1"
            />
            <RelatedVotes issue={issue} headingLevel="h4" />
            <Coverage issue={issue} headingLevel="h4" />
            <div className="mt-8 flex flex-col gap-3 border-t border-white/[0.07] pt-4">
              <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
                <SourceList issue={issue} />
                <a href={`/issue/${issue.publicId}`} className={`${TEXT_LINK} ${TAP_TARGET}`}>
                  Read full story ({issueRef(issue.publicId)}) →
                </a>
              </div>
              <ShareButtons issue={issue} />
            </div>
          </>
        ),
      }}
    />
  );
}

/** The Federal Register's open comment periods, one box each: the agency's
 *  own title, the days left as a countdown (the backend's commentWindow),
 *  the comment link, and the abstract, as the Register printed it, behind
 *  "More". */
function OpenComments() {
  const [items, setItems] = useState<OpenCommentItem[] | null>(null);

  useEffect(() => {
    fetchOpenComments()
      .then(setItems)
      .catch(() => {});
  }, []);

  if (!items || items.length === 0) return null;

  return (
    <BoxGrid
      columns={2}
      label="Open for public comment"
      labelId="open-comments-heading"
      labelHref={OPEN_COMMENTS_HREF}
      labelHrefText="Every open comment period"
      className="mt-12"
    >
      {items.map((item) => {
        const abstract = item.summary || item.firstSentence;
        return (
          <RecordBox
            key={item.id}
            compact
            tone="deadline"
            kicker={
              <>
                <AgencyLink name={item.agencyName} slug={item.agencySlug} />
                {item.agencyName && item.docType ? " · " : ""}
                {item.docType?.replace(/_/g, " ")}
              </>
            }
            title={item.title}
            href={exploreHref(item)}
            figure={item.commentWindow ? { type: "countdown", ...item.commentWindow } : null}
            action={{
              label: "Comment",
              href: `${exploreHref(item)}#comment`,
              ariaLabel: `Comment on ${item.title}`,
            }}
            more={
              abstract
                ? {
                    label: "the agency's summary",
                    children: (
                      <figure>
                        <blockquote>
                          <p>{abstract}</p>
                        </blockquote>
                        <figcaption className="mt-1 text-ink-min">
                          {item.agencyName ? `${item.agencyName}, in the` : "The"} Federal Register
                        </figcaption>
                      </figure>
                    ),
                  }
                : null
            }
          />
        );
      })}
    </BoxGrid>
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
        <BoxGrid
          columns={2}
          // A developing seat flip is listed beside the newest day whatever
          // its own date (backend _latest_current_issues).
          label={
            secondaryIssues.every((i) => i.date === heroIssue.date) ? "Issues this day" : "Issues"
          }
          labelId="issues-heading"
        >
          {issues.map((issue, i) => (
            <IssueBox
              key={issue.id}
              issue={issue}
              today={today}
              lead={i === 0}
              deepLinked={initialIssueId === issue.publicId}
              onToggle={(id, expanded) => onIssueChange?.(expanded ? id : null)}
              onMonitor={onMonitor}
            />
          ))}
        </BoxGrid>
      ) : (
        <p className="border-l-2 border-ink-min/60 py-2 pl-4 font-sans text-base text-ink-lo">
          {selectedDate
            ? // Issues are kept for 14 days unless they were posted
              // (action_center._cleanup_old_unposted_issues); the Archive
              // keeps every day's top issue.
              "Nothing from this day is still on the Action Center. Its top issue is kept in the Archive."
            : "No issues on the record for this day yet. The Action Center refreshes hourly."}
        </p>
      )}

      <OpenComments />
    </div>
  );
}

/** "1:21 PM", or "Oct 14, 4:17 AM" for an earlier day; "" when unparseable. */
function clockTime(iso: string | undefined): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  const time: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit" };
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleString("en-US", time)
    : d.toLocaleString("en-US", { month: "short", day: "numeric", ...time });
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

  // When the news was last read, and when a story last changed. They differ
  // on an hour that found nothing new: a single "Updated" naming the last
  // change read as a refresh that had stopped (2026-10-15).
  const checked = selectedDate ? "" : clockTime(data?.checkedAt);
  const changed = selectedDate ? "" : clockTime(data?.generatedAt);

  // 44px on a phone (mobile spec P10).
  const link =
    "inline-flex min-h-11 items-center sm:min-h-0 text-ink-lo hover:text-ink-hi disabled:cursor-not-allowed disabled:text-ink-min/50";

  return (
    <nav aria-label="Day" className="grid justify-items-end gap-1.5 font-mono text-xs">
      <div className="flex items-baseline gap-4">
        <button
          onClick={() => prev && onSelect(prev)}
          disabled={!prev}
          className={link}
          aria-label={prev ? `Previous day, ${short(prev)}` : "No earlier day"}
        >
          ← {prev ? short(prev) : "Earlier"}
        </button>
        <span className="text-ink-hi">
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
      {(checked || changed) && (
        <span className="text-ink-min">
          {checked && `Checked ${checked}`}
          {checked && changed && changed !== checked && " · "}
          {changed && changed !== checked && `stories updated ${changed}`}
        </span>
      )}
      {selectedDate && (
        <button onClick={() => onSelect(null)} className={`${link}`}>
          Back to the latest
        </button>
      )}
    </nav>
  );
}

// The masthead's line, sent with the server's fallback too (PageFallback).
const ACTION_EYEBROW = "Action Center · what is moving right now";
const ACTION_TITLE = "Today on the record";
const ACTION_LEDE =
  "Issues from the news, and who in Congress can act. Every item links to its source.";

export default function ActionPage() {
  return (
    <Suspense
      fallback={
        <PageFallback
          eyebrow={ACTION_EYEBROW}
          title={ACTION_TITLE}
          lede={ACTION_LEDE}
          width="max-w-4xl"
          mastheadClassName=""
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
            eyebrow={ACTION_EYEBROW}
            title={ACTION_TITLE}
            aside={
              activeTab === "issues" ? (
                <DayPager data={request.data} selectedDate={selectedDate} onSelect={selectDate} />
              ) : undefined
            }
          >
            {ACTION_LEDE}
          </PageMasthead>

          {/* Tab bar.

              `overflow-y-hidden` is load-bearing, not tidying: `overflow-x-auto`
              also makes overflow-y `auto`, and the tabs measure 42.67px against
              a 42px content box, so that fractional pixel is enough to raise a
              vertical scrollbar. globals.css paints every scrollbar thumb in
              full-strength phosphor, so it rendered as a bright green 4px bar
              parked beside the tab row, reading as a deliberate accent.

              It sticks directly under the header at the header's measured
              height (`--header-height`), and at the top edge while a phone's
              header is hidden: it was a literal 82px against an 86px header,
              so it tucked 4px under it, and 34px short of the phone's 52px
              one. Solid, like the header: paper, not glass. */}
          <div
            role="tablist"
            aria-label="Action Center sections"
            data-sticky-subnav
            className="sticky top-[var(--header-shown-h,var(--header-height))] z-30 -mx-4 mb-8 mt-6 flex gap-0 overflow-x-auto overflow-y-hidden border-b border-white/15 bg-surface-base px-4 sm:mx-0 sm:px-0"
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
                className={`-mb-px whitespace-nowrap border-b-3 px-3 py-3 font-mono text-xs transition-colors sm:px-5 ${tabControl(
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
