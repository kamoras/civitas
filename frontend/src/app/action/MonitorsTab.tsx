"use client";

import { useEffect, useRef, useState } from "react";
import { fetchMonitors, fetchMonitorDetail } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { formatUtcDate, safeHref } from "@/lib/formatting";
import { SECTION_HEADING, TEXT_LINK } from "@/components/action/IssueEnrichment";
import type { MonitorUpdate, NationalMonitor } from "@/lib/api";

/** "Sep 29 at 9:14 AM" from the update's own timestamp, else its date. */
function updateTime(update: MonitorUpdate): string {
  const created = update.createdAt ? new Date(update.createdAt) : null;
  if (!created || Number.isNaN(created.getTime())) {
    return formatUtcDate(update.date, { month: "short", day: "numeric", year: "numeric" });
  }
  return `${created.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })} at ${created.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
}

const shortDate = (d: string) => formatUtcDate(d, { month: "short", day: "numeric" });

/** A monitor's dated updates, each with its source. Fetched when first opened. */
function MonitorUpdates({ slug }: { slug: string }) {
  const request = useAsyncData(`action-monitor:${slug}`, () => fetchMonitorDetail(slug));

  if (request.loading) {
    return (
      <p role="status" className="py-3 font-mono text-xs tracking-[0.12em] text-ink-min">
        Reading updates…
      </p>
    );
  }
  if (request.error !== null || !request.data) {
    return (
      <p role="alert" className="py-3 font-mono text-xs text-signal-red">
        Could not load this monitor&apos;s updates.{" "}
        <button onClick={request.retry} className={TEXT_LINK}>
          Try again
        </button>
      </p>
    );
  }

  const updates = request.data.updates ?? [];
  if (updates.length === 0) {
    return <p className="py-3 font-display text-sm text-ink-lo">No updates recorded yet.</p>;
  }

  return (
    <ol className="mt-2 border-l border-white/15 pl-4">
      {updates.map((u) => (
        <li key={u.id} className="py-2.5">
          <span className="font-mono text-xs tracking-[0.08em] text-ink-min">{updateTime(u)}</span>
          <p className="mt-1 font-display text-[15px] leading-relaxed text-ink">{u.summary}</p>
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
  );
}

function MonitorRow({
  monitor,
  initiallyOpen,
}: {
  monitor: NationalMonitor;
  initiallyOpen: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const ref = useRef<HTMLLIElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const active = monitor.status === "active";
  const statusLabel = active ? "Active" : monitor.status === "watching" ? "Watching" : "Closed";
  const areas = monitor.policyAreas ?? [];

  // Arrived from a "Tracked in …" link: bring this row into view, and move
  // focus to it, since the link that was followed is gone from the page.
  useEffect(() => {
    if (!initiallyOpen) return;
    const t = setTimeout(() => {
      buttonRef.current?.focus({ preventScroll: true });
      ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
    return () => clearTimeout(t);
  }, [initiallyOpen]);

  return (
    <li
      ref={ref}
      id={`monitor-${monitor.slug}`}
      className="scroll-mt-[calc(var(--header-clearance)+3rem)] border-b border-white/[0.07]"
    >
      <h3>
        <button
          ref={buttonRef}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={`monitor-detail-${monitor.slug}`}
          className="flex w-full items-start gap-4 py-4 text-left"
        >
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 font-mono text-xs tracking-[0.08em] text-ink-min">
              <span className={`uppercase tracking-[0.1em] ${active ? "text-phos-mid" : ""}`}>
                {statusLabel}
              </span>
              <span>Updated {shortDate(monitor.lastArticleDate || monitor.updatedAt)}</span>
              <span>
                {monitor.updateCount} update{monitor.updateCount !== 1 ? "s" : ""}
              </span>
              {/* A long-running monitor accumulates one policy area per
                  smaller monitor merged into it over time (see backend's
                  _merge_monitors) — past a handful they read as noise. */}
              {areas.length > 0 && (
                <span className="text-ink-lo">
                  {areas.slice(0, 3).join(" · ")}
                  {areas.length > 3 && ` +${areas.length - 3}`}
                </span>
              )}
            </span>
            <span className="mt-1.5 block font-display text-lg font-semibold leading-snug text-ink-hi">
              {monitor.title}
            </span>
            {!open && monitor.description && (
              <span className="mt-1 line-clamp-2 block font-display text-[15px] leading-relaxed text-ink-lo">
                {monitor.description}
              </span>
            )}
          </span>
          <span
            className="mt-0.5 shrink-0 font-mono text-lg leading-none text-ink-min"
            aria-hidden="true"
          >
            {open ? "−" : "+"}
          </span>
        </button>
      </h3>

      {open && (
        <div id={`monitor-detail-${monitor.slug}`} className="pb-5">
          {monitor.description && (
            <p className="max-w-3xl font-display text-[15px] leading-relaxed text-ink">
              {monitor.description}
            </p>
          )}
          <p className="mt-2 font-mono text-xs tracking-[0.08em] text-ink-min">
            Tracking since {shortDate(monitor.createdAt)}
          </p>
          <MonitorUpdates slug={monitor.slug} />
        </div>
      )}
    </li>
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
        className="mb-6 border-l-2 border-ink-min/60 py-2 pl-4 font-display text-base text-ink-lo"
      >
        {gone ? (
          "That concern is no longer tracked, and its record has been removed."
        ) : (
          <>
            Could not load that concern right now.{" "}
            <button onClick={request.retry} className={TEXT_LINK}>
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
      <ul>
        <MonitorRow monitor={request.data} initiallyOpen />
      </ul>
    </section>
  );
}

export default function MonitorsTab({ initialSlug }: { initialSlug?: string | null }) {
  const request = useAsyncData("action-monitors", fetchMonitors);

  if (request.loading) {
    return (
      <p
        role="status"
        aria-live="polite"
        className="py-10 font-mono text-sm tracking-[0.12em] text-ink-min"
      >
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
        <button onClick={request.retry} className={TEXT_LINK}>
          Try again
        </button>
      </div>
    );
  }

  const monitors = request.data?.monitors ?? [];
  const offList = initialSlug && !monitors.some((m) => m.slug === initialSlug) ? initialSlug : null;

  return (
    <div>
      <p className="mb-6 max-w-2xl font-display text-base leading-relaxed text-ink-lo">
        Concerns that keep coming back across days of coverage, detected automatically. Each one
        gathers the dated updates that fed it, with a source for every entry.
      </p>

      {offList && <OffListMonitor slug={offList} />}

      <section aria-labelledby="monitors-heading">
        <h2 id="monitors-heading" className={SECTION_HEADING}>
          <span>Tracking</span>
          <span aria-hidden="true">
            {monitors.length} concern{monitors.length !== 1 ? "s" : ""}
          </span>
        </h2>
        {monitors.length === 0 ? (
          <p className="py-4 font-display text-base text-ink-lo">
            Nothing is being tracked right now. A monitor opens when an issue persists across
            several days of coverage.
          </p>
        ) : (
          <ul>
            {monitors.map((m) => (
              <MonitorRow key={m.slug} monitor={m} initiallyOpen={m.slug === initialSlug} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
