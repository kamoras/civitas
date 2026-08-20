export function formatCurrency(amount: number): string {
  // Compact on magnitude, then re-attach the sign OUTSIDE the "$" so a
  // negative reads "-$1.0M", not "$-1,000,000". Operating on the raw value
  // skipped every threshold for negatives and fell through to the plain
  // toLocaleString branch (e.g. a negative million rendered "$-1,000,000").
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount);
  if (abs >= 1_000_000_000) {
    return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`;
  }
  if (abs >= 1_000_000) {
    return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  }
  if (abs >= 1_000) {
    return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  }
  // Rounded, not raw — FEC cash-on-hand figures carry cents (e.g.
  // 383.2, 944.54), and every other tier above already drops sub-unit
  // precision (a $1.2M figure doesn't show its cents either); showing
  // "$383.2" vs "$200" side by side in the same list read as
  // inconsistent/buggy rather than as real precision (2026-08 review).
  return `${sign}$${Math.round(abs).toLocaleString()}`;
}

/** Returns the local date as "YYYY-MM-DD" — never UTC, so it matches the user's calendar. */
export function localDateStr(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * Format a date string ("YYYY-MM-DD") for display using the browser's locale.
 * Parses as local noon so the calendar date is always preserved regardless of timezone.
 */
export function formatUtcDate(
  dateStr: string,
  opts: Intl.DateTimeFormatOptions = { year: "numeric", month: "long", day: "numeric" },
  locale?: string
): string {
  if (!dateStr) return "";
  try {
    return new Date(dateStr + "T12:00:00").toLocaleDateString(locale, opts);
  } catch {
    return dateStr;
  }
}

/**
 * A story's date, honestly. `date` is bumped to today on every pipeline run
 * that re-matches an ActionIssue to fresh coverage, whether or not anything
 * changed, so a week-old story still trending shows today's date as if
 * that's when it happened. When the two differ, say both rather than pick
 * one: "2026-08-19 · updated 2026-08-20".
 */
export function issueDateLabel(issue: { date: string; firstSurfaced: string }): string {
  return issue.firstSurfaced === issue.date
    ? issue.date
    : `${issue.firstSurfaced} · updated ${issue.date}`;
}

/**
 * Format a Monday–Sunday span for display: "Jul 13–19, 2026", or
 * "Jun 29–Jul 5, 2026" when the week crosses a month boundary.
 *
 * The end date is built from separate single-field lookups because
 * { day, year } is not a CLDR skeleton — ICU best-fits the pair and renders
 * "2026 (day: 19)", so the week header read "Jul 13–2026 (day: 19)".
 */
export function formatWeekRange(startDate: string, endDate: string): string {
  const start = new Date(startDate + "T00:00:00");
  const end = new Date(endDate + "T00:00:00");
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return `${startDate}–${endDate}`;
  }
  const startFmt = start.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const endFmt = end.toLocaleDateString(
    "en-US",
    start.getMonth() === end.getMonth() ? { day: "numeric" } : { month: "short", day: "numeric" }
  );
  return `${startFmt}–${endFmt}, ${end.getFullYear()}`;
}

const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

export function safeHref(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  // Reject protocol-relative URLs (//evil.com) before URL parsing
  if (url.trimStart().startsWith("//")) return undefined;
  try {
    const parsed = new URL(url, "https://placeholder.invalid");
    if (SAFE_PROTOCOLS.has(parsed.protocol)) return url;
  } catch {
    /* malformed URL */
  }
  return undefined;
}

/** Parses an ISO-8601 timestamp, treating an offset-less string as UTC —
 * `new Date("2026-07-04T12:00:00")` would otherwise parse as viewer-local
 * time (repo precedent: admin/page.tsx's `new Date(startIso + "Z")`).
 *
 * This is not a hypothetical: the backend's `utcnow()` deliberately returns a
 * NAIVE UTC datetime (see backend/app/time_utils.py), so Pydantic serialises
 * every timestamp without a `Z` or an offset. Passing one of those straight
 * to `new Date` silently shifts it by the viewer's UTC offset.
 * Returns null for unparseable input.
 */
export function parseUtc(iso: string): Date | null {
  const hasTime = /[T ]\d{2}:\d{2}/.test(iso);
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasTime && !hasOffset ? `${iso}Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Days remaining until a comment period closes, phrased for a reader.
 *
 * `asOf` is passed in rather than read from the clock: a countdown computed
 * during render would change without any input changing, which is both impure
 * and untestable. Callers read the clock once, when the deadline arrives.
 */
export function describeDaysLeft(closeDate: string, asOf: number): string {
  const close = parseUtc(closeDate);
  if (!close) return "";
  const diff = Math.ceil((close.getTime() - asOf) / 86400000);
  if (diff <= 0) return "closes today";
  if (diff === 1) return "1 day left";
  return `${diff} days left`;
}
