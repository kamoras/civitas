import Link from "next/link";
import { connection } from "next/server";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue } from "@/types/action";
import type { BillInFlight } from "@/types/bill";
import { ACTION_CENTER_HREF, monitorHref } from "@/lib/routes";
import { issueRef, parseUtc, policyAreaLabel, restatesTitle } from "@/lib/formatting";
import { billHref } from "@/lib/congress";

/**
 * The dense, dated index of what has recently entered the record.
 *
 * Replaces the single hero card the homepage used to show. The point of the
 * change is the docket reference on every row: an entry you can quote at
 * someone is worth more to a research site than a headline, and it is a
 * filing-cabinet habit rather than a product feed.
 *
 * Rendered on the server with the page, so it can sit in the first screen
 * without arriving after hydration and pushing everything below it down.
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
  source: keyof typeof SOURCE_ORDER;
}

const MAX_ENTRIES = 6;

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Within one day, the day's issues come first, in the Action Center's own
// rank order (the endpoint sends them that way), so the first row is the
// day's top issue: issue dates are date-only (midnight), and a bill or
// monitor timestamped that afternoon sorted above every one of them.
const SOURCE_ORDER = { issue: 0, monitor: 1, bill: 2 } as const;

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

function utcDay(d: Date): number {
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
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
      detail:
        i.summary && !restatesTitle(i.title, i.summary)
          ? clamp(i.summary, 78)
          : i.policyAreas.map(policyAreaLabel).join(" · "),
      href: `/issue/${i.publicId}`,
      tone: "text-phos-mid",
      source: "issue" as const,
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
        href: monitorHref(m.slug),
        tone: "text-signal-orange",
        source: "monitor" as const,
      })),
    ...bills.map((b) => ({
      ref: b.billId.toUpperCase(),
      date: b.latestActionDate,
      title: b.title,
      detail: clamp(b.latestAction, 78),
      href: billHref(b.billId, b.congress),
      tone: "text-signal-cyan",
      source: "bill" as const,
    })),
  ];

  return entries
    .map((e) => ({ entry: e, at: e.date ? parseUtc(e.date) : null }))
    .filter((x): x is { entry: RecordEntry; at: Date } => Boolean(x.entry.title && x.at))
    .sort(
      (a, b) =>
        utcDay(b.at) - utcDay(a.at) ||
        SOURCE_ORDER[a.entry.source] - SOURCE_ORDER[b.entry.source] ||
        b.at.getTime() - a.at.getTime()
    )
    .slice(0, MAX_ENTRIES)
    .map((x) => x.entry);
}

async function getList<T>(path: string, key: string, revalidate: number): Promise<T[]> {
  // Each feed fails independently: one dead endpoint thins this list, it
  // doesn't blank it.
  try {
    const res = await fetch(`${BACKEND}${path}`, { next: { revalidate } });
    if (!res.ok) return [];
    const list = ((await res.json()) as Record<string, unknown>)[key];
    return Array.isArray(list) ? (list as T[]) : [];
  } catch {
    return [];
  }
}

export default async function RecordIndex() {
  // Per request, never prerendered: `next build` can't reach the backend.
  await connection();
  const [issues, monitors, bills] = await Promise.all([
    // Not /action/issues: that one is is_current-filtered (right for the
    // Action Center's live board, wrong here: an entry vanished from this
    // "record" the instant it retired, per the 2026-08-22 report). This one
    // ignores is_current, so a stopped story sinks rather than disappears.
    // Lifetimes are the backend's own Cache-Control.
    getList<ActionIssue>(`/api/action/issues/recent?limit=${MAX_ENTRIES}`, "issues", 30),
    getList<NationalMonitor>("/api/action/monitors", "monitors", 300),
    getList<BillInFlight>(`/api/bills?sort=recent&per_page=${MAX_ENTRIES}`, "bills", 120),
  ]);
  const entries = buildRecordEntries(issues, monitors, bills);
  if (entries.length === 0) return null;
  return <RecordIndexList entries={entries} />;
}

export function RecordIndexList({ entries }: { entries: RecordEntry[] }) {
  return (
    <section aria-labelledby="record-index" className="mx-auto mt-8 max-w-7xl px-4 sm:px-6">
      {/* The link sits beside the heading, not in it, so the heading's name
          is the heading (as in OnTheRecord). */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        <h2 id="record-index">Entered into the record</h2>
        <Link
          href={ACTION_CENTER_HREF}
          className="normal-case tracking-[0.06em] underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          The Action Center today &rarr;
        </Link>
      </div>
      <ul className="grid grid-cols-1 gap-x-8 sm:grid-cols-2 lg:grid-cols-3">
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
    </section>
  );
}
