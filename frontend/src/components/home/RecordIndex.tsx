"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchActionIssues, fetchBillsInFlight, fetchMonitors } from "@/lib/api";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue } from "@/types/action";
import type { BillInFlight } from "@/types/bill";
import { ACTION_CENTER_MONITORS_HREF } from "@/lib/routes";

/**
 * The dense, dated index of what has recently entered the record.
 *
 * Replaces ActionPreview's single hero card on the homepage. The point of the
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

/** "2026-08-18" -> "18 AUG". Returns "" for anything unparseable. */
export function formatEntryDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
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
      ref: `ISSUE-${i.id}`,
      date: i.date,
      title: i.title,
      detail: i.summary ? clamp(i.summary, 78) : i.policyAreas.join(" · "),
      href: `/issue/${i.id}`,
      tone: "text-phos-mid",
    })),
    // Only monitors the backend still considers live: a closed monitor is
    // history, and this list is "what changed lately".
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
    .filter((e) => e.title && !Number.isNaN(new Date(e.date).getTime()))
    .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
    .slice(0, MAX_ENTRIES);
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
