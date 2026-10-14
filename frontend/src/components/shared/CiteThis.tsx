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

/** An APA-style reference to a page of the record, as a dataset: the site,
 *  the record's date ("n.d." for a record that keeps changing), its title,
 *  the date the reader retrieved it, and its stable URL. */
export function citation(
  title: string,
  date: string | null | undefined,
  path: string,
  today: Date
) {
  const retrieved = `${MONTHS[today.getMonth()]} ${today.getDate()}, ${today.getFullYear()}`;
  return `${SITE_NAME}. (${apaDate(date) ?? "n.d."}). ${title} [Data set]. Retrieved ${retrieved}, from ${absoluteUrl(path)}`;
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
}: {
  title: string;
  /** YYYY-MM-DD the record is of, when it has one. */
  date?: string | null;
  /** The record's canonical path. */
  path: string;
}) {
  const mounted = useMounted();
  if (!mounted) {
    return (
      <p className="font-mono text-xs text-ink-min" aria-live="polite">
        Preparing the citation…
      </p>
    );
  }
  return <CopyText text={citation(title, date, path, new Date())} label="citation" />;
}
