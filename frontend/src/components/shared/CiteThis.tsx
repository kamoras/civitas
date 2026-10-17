"use client";

import CopyText from "@/components/CopyText";
import { useMounted } from "@/components/elections/CoverageFeed";
import { SITE_NAME, absoluteUrl } from "@/lib/site";

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** "2026-10-13" -> "2026, October 13" (APA's date order); null for anything
 *  else. Split by hand: `new Date("2026-10-13")` is UTC midnight and reads as
 *  the day before west of Greenwich. */
export function apaDate(iso: string | null | undefined): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!m || !MONTHS[Number(m[2]) - 1]) return null;
  return `${m[1]}, ${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}`;
}

/** The record's own author and where it was published, for a page about a
 *  government document or record: the agency and the Federal Register for a
 *  rule, the chamber for a roll call, the court for an opinion. */
export interface CitedSource {
  /** Who issued it ("Internal Revenue Service", "United States Senate"). */
  author: string;
  /** Where it was published, with its official identifier ("Federal
   *  Register, 91, 54280 (FR Doc. 2026-17123)"); omitted when the author
   *  published it themselves. */
  container?: string | null;
  /** The official copy. */
  url: string;
}

/** `text` ended by one full stop: a title that already ends a sentence
 *  ("…Res. 14.", "…Act?") keeps its own mark. */
function sentence(text: string): string {
  const t = text.trim();
  return /[.?!]$/.test(t) ? t : `${t}.`;
}

/** An APA-style reference to a page of the record. A government document or
 *  record cites its issuing body as author, its official identifier and its
 *  official URL, with Civitas only as where it was accessed (`source`).
 *  Without one, the page is Civitas's own work (a scorecard, an analysis)
 *  and cites Civitas, as a dataset: the record's date ("n.d." for a record
 *  that keeps changing), its title, the reader's retrieval date and the
 *  page's stable URL. */
export function citation(
  title: string,
  date: string | null | undefined,
  path: string,
  today: Date,
  source?: CitedSource | null
) {
  const retrieved = `${MONTHS[today.getMonth()]} ${today.getDate()}, ${today.getFullYear()}`;
  const when = apaDate(date) ?? "n.d.";
  if (source) {
    const container = source.container ? ` ${sentence(source.container)}` : "";
    return `${sentence(source.author)} (${when}). ${sentence(title)}${container} ${source.url} (accessed via ${SITE_NAME}, ${retrieved}, at ${absoluteUrl(path)})`;
  }
  return `${SITE_NAME}. (${when}). ${title.trim().replace(/\.$/, "")} [Data set]. Retrieved ${retrieved}, from ${absoluteUrl(path)}`;
}

/**
 * "Cite this": the reference for the page, selectable, with a copy button.
 * Every part comes from the record (its title, its date, its canonical path)
 * except the retrieval date, the reader's own today, which is filled in once
 * the page is in the reader's browser: rendered on the server it would be the
 * server's date and could disagree with the reader's.
 */
export default function CiteThis({
  title,
  date,
  path,
  source,
}: {
  title: string;
  /** YYYY-MM-DD the record is of, when it has one. */
  date?: string | null;
  /** The record's canonical path. */
  path: string;
  /** For a government document or record: who issued it and where. */
  source?: CitedSource | null;
}) {
  const mounted = useMounted();
  if (!mounted) {
    return (
      <p className="font-mono text-xs text-ink-min" aria-live="polite">
        Preparing the citation…
      </p>
    );
  }
  return <CopyText text={citation(title, date, path, new Date(), source)} label="citation" />;
}
