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
  return `${sign}$${abs.toLocaleString()}`;
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
  locale?: string,
): string {
  if (!dateStr) return "";
  try {
    return new Date(dateStr + "T12:00:00").toLocaleDateString(locale, opts);
  } catch {
    return dateStr;
  }
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
    start.getMonth() === end.getMonth() ? { day: "numeric" } : { month: "short", day: "numeric" },
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
