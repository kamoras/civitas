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
