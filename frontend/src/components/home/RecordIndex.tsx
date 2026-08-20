"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchActionIssues, fetchBillsInFlight, fetchMonitors } from "@/lib/api";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue } from "@/types/action";
import type { BillInFlight } from "@/types/bill";
import { ACTION_CENTER_MONITORS_HREF } from "@/lib/routes";
import { issueRef, parseUtc } from "@/lib/formatting";

/**
 * The dense, dated index of what has recently entered the record.
 *
 * Replaces the single hero card the homepage used to show. The point of the
 * change is the docket reference on every row: an entry you can quote at
 * someone is worth more to a research site than a headline, and it is a
 * filing-cabinet habit rather than a product feed.
 *
 * Sources are the three the backend already exposes. A dedicated "record
 * changelog" endpoint — filings ingested, scores recomputed, corrections
 * issued — would be the natural thing to show here; it does not exist yet,
 * so this composes what does rather than inventing entries.
 */

export interface RecordEntry {
  /** Docket-style reference, unique across sources. */
  ref: string;
  /** ISO date the entry is filed under. */
  date: string;
  title: string;
  detail: string;
  href: string;
  /** Tailwind text colour for the reference line. */
  tone: string;
}

const MAX_ENTRIES = 6;

/**
 * "2026-08-18" -> "18 AUG". Returns "" for anything unparseable.
 *
 * `parseUtc`, not `new Date`: issue dates are date-only (which ECMA-262 does
 * parse as UTC) but monitor and bill timestamps are full date-times that the
 * backend serialises without a `Z`, and those parse as viewer-local. Mixing
 * the two would not only mislabel a day, it would make the sort in
 * buildRecordEntries inconsistent between sources near midnight.
 */
export function formatEntryDate(iso: string): string {
  const d = parseUtc(iso);
  if (!d) return "";
  const month = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }).toUpperCase();
  return `${String(d.getUTCDate()).padStart(2, "0")} ${month}`;
}

/** Trim to a whole word so a detail line never ends mid-token. */
function clamp(text: string, max: number): string {
  const t = text.trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).replace(/[,;:.\s]+$/, "")}…`;
}

/**
 * Merge the three feeds into one dated index, newest first.
 *
 * Exported for tests: the merge/sort/truncate is the part with actual logic
 * in it, and it is worth pinning independently of the fetches.
 */
export function buildRecordEntries(
  issues: ActionIssue[],
  monitors: NationalMonitor[],
  bills: BillInFlight[]
): RecordEntry[] {
  const entries: RecordEntry[] = [
    ...issues.map((i) => ({
      // publicId, not the raw autoincrement id — this ref is the whole
      // point of the component ("an entry you can quote at someone"), so
      // quoting it can't read as a running count of every issue ever.
      ref: issueRef(i.publicId),
      date: i.date,
      title: i.title,
      detail: i.summary ? clamp(i.summary, 78) : i.policyAreas.join(" · "),
      href: `/issue/${i.publicId}`,
      tone: "text-phos-mid",
    })),
    // ACTIVE only. The endpoint already filters to ACTIVE + WATCHING, so
    // this drops WATCHING — a monitor gone dormant past the article cutoff
    // (see MonitorStatus in backend/app/models.py). That is deliberate and
    // not merely incidental: a dormant monitor often has a null
    // lastArticleDate, so it would fall back to `updatedAt`, which the
    // pipeline touches when it flips the status — surfacing a stale monitor
    // at the top of a list whose whole claim is "what changed lately".
    ...monitors
      .filter((m) => m.status === "active")
      .map((m) => ({
        ref: `MON-${m.id}`,
        date: m.lastArticleDate || m.updatedAt,
        title: m.title,
        detail: `${m.updateCount} update${m.updateCount === 1 ? "" : "s"} tracked`,
        href: ACTION_CENTER_MONITORS_HREF,
        tone: "text-signal-orange",
      })),
    ...bills.map((b) => ({
      ref: b.billId.toUpperCase(),
      date: b.latestActionDate,
      title: b.title,
      detail: clamp(b.latestAction, 78),
      href: `/bills/${encodeURIComponent(b.billId)}`,
      tone: "text-signal-cyan",
    })),
  ];

  return entries
    .map((e) => ({ entry: e, at: e.date ? parseUtc(e.date) : null }))
    .filter((x): x is { entry: RecordEntry; at: Date } => Boolean(x.entry.title && x.at))
    .sort((a, b) => b.at.getTime() - a.at.getTime())
    .slice(0, MAX_ENTRIES)
    .map((x) => x.entry);
}

export default function RecordIndex() {
  const [entries, setEntries] = useState<RecordEntry[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // Each feed fails independently: one dead endpoint should thin this list,
    // not blank it. Promise.all over already-caught promises never rejects.
    Promise.all([
      fetchActionIssues()
        .then((d) => d.issues ?? [])
        .catch((): ActionIssue[] => []),
      fetchMonitors()
        .then((d) => d.monitors ?? [])
        .catch((): NationalMonitor[] => []),
      fetchBillsInFlight({ sort: "recent", perPage: 6 })
        .then((d) => d.bills ?? [])
        .catch((): BillInFlight[] => []),
    ])
      .then(([issues, monitors, bills]) => setEntries(buildRecordEntries(issues, monitors, bills)))
      .finally(() => setLoaded(true));
  }, []);

  if (loaded && entries.length === 0) return null;

  return (
    <section className="md:col-span-8">
      <h2 className="flex items-baseline justify-between border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        <span>Entered into the record</span>
        <span aria-hidden="true">Latest</span>
      </h2>

      {!loaded ? (
        <p role="status" aria-live="polite" className="sr-only">
          Loading the record index…
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
          {entries.map((entry) => (
            <li key={entry.ref} className="border-b border-white/[0.07]">
              <Link href={entry.href} className="group block py-3">
                <span className={`font-mono text-xs tracking-[0.08em] ${entry.tone}`}>
                  {formatEntryDate(entry.date)} · {entry.ref}
                </span>
                <span className="mt-1 block font-display text-base font-semibold leading-snug text-ink-hi group-hover:underline">
                  {entry.title}
                </span>
                {entry.detail && (
                  <span className="mt-0.5 block font-display text-sm leading-snug text-ink-lo">
                    {entry.detail}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
