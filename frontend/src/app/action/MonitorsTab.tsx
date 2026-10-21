"use client";

import { useEffect, useRef, useState } from "react";
import OnTheRecord from "@/components/action/OnTheRecord";
import { fetchMonitors, fetchMonitorDetail } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { retryKeepingFocus } from "@/lib/tabFocus";
import { formatUtcDate, policyAreaLabel, restatesTitle, safeHref } from "@/lib/formatting";
import { OutletLink, SECTION_HEADING, TEXT_LINK } from "@/components/action/IssueEnrichment";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import NoteLine from "@/components/elections/NoteLine";
import type { MonitorUpdate, NationalMonitor } from "@/lib/api";
import { scrollBehavior } from "@/lib/motion";
import FollowFeed from "@/components/shared/FollowFeed";
import { monitorFeed } from "@/lib/site";

/** "Sep 29 at 9:14 AM" from the update's own timestamp, else its date. */
function updateTime(update: MonitorUpdate): string {
  const created = update.createdAt ? new Date(update.createdAt) : null;
  if (!created || Number.isNaN(created.getTime())) {
    return formatUtcDate(update.date, { month: "short", day: "numeric", year: "numeric" });
  }
  return `${created.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })} at ${created.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
}

const shortDate = (d: string) => formatUtcDate(d, { month: "short", day: "numeric" });

// Updates shown when a monitor opens, and how many more each "show more"
// adds. All 303 of the Iran monitor's at once made the tab 77.8 phone
// screens long (2026-10-16).
const UPDATES_PAGE = 10;

/** A monitor's related votes and its dated updates, each with its source,
 *  newest first, a page at a time. Fetched when first opened. */
function MonitorUpdates({ slug, title }: { slug: string; title: string }) {
  const request = useAsyncData(`action-monitor:${slug}`, () => fetchMonitorDetail(slug));
  const [shown, setShown] = useState(UPDATES_PAGE);
  const listRef = useRef<HTMLOListElement>(null);

  if (request.loading) {
    return (
      <p role="status" className="py-3 font-mono text-xs text-ink-min">
        Reading updates…
      </p>
    );
  }
  if (request.error !== null || !request.data) {
    return (
      <p role="alert" className="py-3 font-mono text-xs text-signal-red">
        Could not load this monitor&apos;s updates.{" "}
        <button
          onClick={retryKeepingFocus(request.retry, (b) =>
            b.closest("li")?.querySelector<HTMLElement>("button[aria-expanded]")
          )}
          className={TEXT_LINK}
        >
          Try again
        </button>
      </p>
    );
  }

  const updates = request.data.updates ?? [];
  const votes = request.data.onTheRecord ?? [];
  const rest = updates.length - shown;
  const showMore = () => {
    // Focus the first newly shown update, so a keyboard user carries on
    // reading where the list grew rather than back at the button's old spot.
    const next = shown;
    setShown((n) => n + UPDATES_PAGE);
    requestAnimationFrame(() =>
      listRef.current?.querySelectorAll<HTMLElement>("li")[next]?.focus({ preventScroll: false })
    );
  };

  return (
    <>
      <OnTheRecord slug={slug} title={title} votes={votes} />
      {updates.length === 0 ? (
        <p className="py-3 font-sans text-sm text-ink-lo">No updates recorded yet.</p>
      ) : (
        <>
          <h4 className="mt-6 font-mono text-xs text-ink-lo">
            Updates, newest first · {Math.min(shown, updates.length)} of {updates.length}
          </h4>
          <ol ref={listRef} className="mt-2 border-l border-white/15 pl-4">
            {updates.slice(0, shown).map((u) => (
              <li
                key={u.id}
                tabIndex={-1}
                className="py-2.5 outline-none focus-visible:ring-1 focus-visible:ring-ink-lo"
              >
                <span className="font-mono text-xs text-ink-min">{updateTime(u)}</span>
                <p className="mt-1 font-sans text-[15px] leading-relaxed text-ink">{u.summary}</p>
                <a
                  href={safeHref(u.sourceUrl) || "#"}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-block font-mono text-xs text-ink-lo underline decoration-white/20 underline-offset-4 hover:text-ink-hi"
                >
                  {u.sourceName || u.articleTitle || "Source"} <span aria-hidden="true">↗</span>
                </a>
              </li>
            ))}
          </ol>
          {rest > 0 && (
            <button
              type="button"
              onClick={showMore}
              className="mt-1 inline-flex min-h-[32px] items-center font-mono text-xs text-phos-mid hover:text-phos"
            >
              [ Show {Math.min(rest, UPDATES_PAGE)} more of {rest} ]
            </button>
          )}
        </>
      )}
    </>
  );
}

/**
 * One monitor as a box ("Boxes, not blocks"): its status and areas over its
 * title, its newest update quoted with the outlet it is from (or, where the
 * update's text isn't an outlet's own words, its headline and source), and
 * the updates and last date as its figure. Its description, feed, related
 * votes and every update are behind "More", read when first opened.
 */
function MonitorRow({
  monitor,
  initiallyOpen,
}: {
  monitor: NationalMonitor;
  initiallyOpen: boolean;
}) {
  const [opened, setOpened] = useState(initiallyOpen);
  const active = monitor.status === "active";
  const statusLabel = active ? "Active" : monitor.status === "watching" ? "Watching" : "Closed";
  const areas = monitor.policyAreas ?? [];
  const id = `monitor-${monitor.slug}`;
  const latest = monitor.latest;
  const last = monitor.lastArticleDate || monitor.updatedAt;

  // Arrived from a "Tracked in …" link: bring this box into view, and move
  // focus to its "More", since the link that was followed is gone.
  useEffect(() => {
    if (!initiallyOpen) return;
    const t = setTimeout(() => {
      const box = document.getElementById(id);
      box?.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
      box?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
    }, 100);
    return () => clearTimeout(t);
  }, [initiallyOpen, id]);

  const kicker = [
    statusLabel,
    // A long-running monitor accumulates one policy area per smaller
    // monitor merged into it (backend _merge_monitors): past a handful
    // they read as noise.
    areas.length > 0
      ? `${areas.slice(0, 3).map(policyAreaLabel).join(" · ")}${areas.length > 3 ? ` +${areas.length - 3}` : ""}`
      : null,
    monitor.closedOn
      ? `Tracked ${shortDate(monitor.createdAt)} – ${shortDate(last || monitor.createdAt)} · closed ${shortDate(monitor.closedOn)}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const line = latest?.text
    ? {
        quote: {
          text: latest.text,
          source: (
            <>
              <OutletLink name={latest.source} url={latest.sourceUrl} />, {shortDate(latest.date)}
            </>
          ),
        },
      }
    : latest
      ? {
          dek: (
            <>
              Latest, {shortDate(latest.date)}: {latest.headline || "an update"}
              {latest.source ? ` (${latest.source})` : ""}
            </>
          ),
        }
      : {};
  const description =
    monitor.description && !restatesTitle(monitor.title, monitor.description)
      ? monitor.description
      : "";

  return (
    <RecordBox
      id={id}
      compact
      tone={active ? "record" : "neutral"}
      kicker={kicker}
      title={monitor.title}
      {...line}
      figure={{
        type: "stat",
        value: monitor.updateCount,
        caption: `update${monitor.updateCount !== 1 ? "s" : ""} · last ${shortDate(last)}`,
      }}
      more={{
        label: "every update and the related votes",
        open: initiallyOpen,
        onToggle: (open) => open && setOpened(true),
        children: (
          <div id={`monitor-detail-${monitor.slug}`}>
            {description && (
              <p className="max-w-3xl font-sans text-[15px] leading-relaxed text-ink">
                {description}
              </p>
            )}
            <p className="mt-2 font-mono text-xs text-ink-min">
              Tracking since {shortDate(monitor.createdAt)}
            </p>
            <FollowFeed
              className="mt-2"
              subject="this monitor"
              feed={monitorFeed(monitor.slug, monitor.title)}
            />
            {opened && <MonitorUpdates slug={monitor.slug} title={monitor.title} />}
          </div>
        ),
      }}
    />
  );
}

/**
 * A monitor someone was sent to that the live list doesn't carry. The list is
 * active and watching monitors only; one a month without coverage closes it
 * (or deletes it, when it never gathered enough), while archive entries and
 * older issues still name it. The detail endpoint still serves a closed one.
 */
function OffListMonitor({ slug }: { slug: string }) {
  const request = useAsyncData(`action-monitor:${slug}`, () => fetchMonitorDetail(slug));
  const noteRef = useRef<HTMLParagraphElement>(null);
  const missing = request.error !== null;

  // The link that brought the reader here is gone from the page; give focus
  // somewhere that says what happened rather than dropping it on <body>.
  useEffect(() => {
    if (missing) noteRef.current?.focus();
  }, [missing]);

  if (request.loading) return null;
  if (missing || !request.data) {
    const gone = request.error?.includes("404");
    return (
      <p
        ref={noteRef}
        tabIndex={-1}
        role="status"
        className="mb-6 border-l-2 border-ink-min/60 py-2 pl-4 font-sans text-base text-ink-lo"
      >
        {gone ? (
          "That concern is no longer tracked, and its record has been removed."
        ) : (
          <>
            Could not load that concern right now.{" "}
            <button onClick={retryKeepingFocus(request.retry)} className={TEXT_LINK}>
              Try again
            </button>
          </>
        )}
      </p>
    );
  }
  return (
    <section aria-labelledby="off-list-heading" className="mb-10">
      <h2 id="off-list-heading" className={SECTION_HEADING}>
        No longer tracked
      </h2>
      <BoxGrid columns={1}>
        <MonitorRow monitor={request.data} initiallyOpen />
      </BoxGrid>
    </section>
  );
}

export default function MonitorsTab({ initialSlug }: { initialSlug?: string | null }) {
  const request = useAsyncData("action-monitors", fetchMonitors);

  if (request.loading) {
    return (
      <p role="status" aria-live="polite" className="py-10 font-mono text-sm text-ink-min">
        Reading the national monitors…
      </p>
    );
  }

  if (request.error !== null) {
    return (
      <div
        role="alert"
        className="flex flex-wrap items-baseline justify-between gap-3 border-l-2 border-signal-red bg-surface px-4 py-3 font-mono text-sm text-signal-red"
      >
        <span>Could not load the national monitors.</span>
        <button onClick={retryKeepingFocus(request.retry)} className={TEXT_LINK}>
          Try again
        </button>
      </div>
    );
  }

  const monitors = request.data?.monitors ?? [];
  const closed = request.data?.recentlyClosed ?? [];
  const closedDays = request.data?.recentlyClosedDays ?? 60;
  const listed = [...monitors, ...closed];
  const offList = initialSlug && !listed.some((m) => m.slug === initialSlug) ? initialSlug : null;

  return (
    <div>
      <div className="mb-6 max-w-2xl">
        <NoteLine lead="Concerns that keep coming back across days of coverage.">
          Detected automatically. Each one gathers the dated updates that fed it, with a source for
          every entry. One with no new coverage for a week is marked watching until coverage
          returns; after a month it closes. A closed one stays listed here for {closedDays} days,
          and in the Archive.
        </NoteLine>
      </div>

      {offList && <OffListMonitor slug={offList} />}

      <section aria-labelledby="monitors-heading">
        <h2 id="monitors-heading" className={SECTION_HEADING}>
          <span>Tracking</span>
          <span aria-hidden="true">
            {monitors.length} concern{monitors.length !== 1 ? "s" : ""}
          </span>
        </h2>
        {monitors.length === 0 ? (
          <p className="py-4 font-sans text-base text-ink-lo">
            Nothing is being tracked right now. A monitor opens when a story comes back on five
            separate days within two weeks.
          </p>
        ) : (
          <BoxGrid columns={2}>
            {monitors.map((m) => (
              <MonitorRow key={m.slug} monitor={m} initiallyOpen={m.slug === initialSlug} />
            ))}
          </BoxGrid>
        )}
      </section>

      {/* With one stale card the tab read as abandoned; what closed recently
          is part of the record of what was tracked. */}
      {closed.length > 0 && (
        <section aria-labelledby="monitors-closed-heading" className="mt-10">
          <h2 id="monitors-closed-heading" className={SECTION_HEADING}>
            <span>Closed in the last {closedDays} days</span>
            <span aria-hidden="true">{closed.length}</span>
          </h2>
          <BoxGrid columns={2}>
            {closed.map((m) => (
              <MonitorRow key={m.slug} monitor={m} initiallyOpen={m.slug === initialSlug} />
            ))}
          </BoxGrid>
        </section>
      )}
    </div>
  );
}
