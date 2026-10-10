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
  // Except under a dollar, where rounding would print a real, nonzero
  // figure as "$0" (a campaign at -$0.04 read "$0"): there the cents are
  // the figure. FEC amounts are in cents, so a nonzero one is at least $0.01.
  if (abs > 0 && abs < 1) {
    return `${sign}$${Math.max(abs, 0.01).toFixed(2)}`;
  }
  return `${sign}$${Math.round(abs).toLocaleString()}`;
}

/** Cash on hand exactly as the FEC reports it, sign included: a negative
 * figure reads "Cash on hand -$4K". It used to be relabelled "Debt" and
 * shown unsigned, but the FEC reports a committee's debts as a separate
 * figure, so that label named a number we weren't showing. Shared by
 * CandidateCard, RaceMoneyBars and RaceFullDetail's tail rows so they
 * don't drift. */
export function cashOnHandDisplay(
  cashOnHand: number | null
): { label: string; amount: string } | null {
  if (cashOnHand == null) return null;
  return { label: "Cash on hand", amount: formatCurrency(cashOnHand) };
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
 *
 * `firstSurfaced` falsy (missing, not merely equal to `date`) falls back to
 * `date` alone rather than interpolating "undefined" into the page — a real
 * case, not a hypothetical: nginx's proxy_cache for this endpoint can still
 * be serving a response cached from BEFORE a deploy that added this field,
 * for up to its own TTL regardless of how fresh the backend already is.
 */
export function issueDateLabel(issue: { date: string; firstSurfaced?: string }): string {
  return issue.firstSurfaced && issue.firstSurfaced !== issue.date
    ? `${issue.firstSurfaced} · updated ${issue.date}`
    : issue.date;
}

/**
 * The "ISSUE-XXXXXXXX" docket reference — upper-cased to match ISSUE's own
 * capitalization. `publicId` typed optional and guarded, not just because
 * TypeScript allows a caller to pass one: the exact stale-cache window
 * issueDateLabel's own fallback guards against (an nginx response cached
 * from before a deploy that added a field) applies here too, and calling
 * `.toUpperCase()` on a genuinely missing value throws — a crash, not just
 * a display glitch like the date label's.
 */
export function issueRef(publicId: string | undefined): string {
  return `ISSUE-${(publicId ?? "").toUpperCase()}`;
}

/**
 * Whether `fact` should carry the [NEW] marker. Guards the same way
 * issueRef/issueDateLabel do: `newFacts` typed non-optional, but a
 * response cached (browser or nginx) from before the field existed won't
 * have it — confirmed live, this exact gap crashed the whole Action
 * Center (`issue.newFacts.includes` with no guard) for every visitor
 * whose browser held one of those responses, 2026-08-20.
 */
export function isNewFact(newFacts: string[] | undefined, fact: string): boolean {
  return (newFacts ?? []).includes(fact);
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
 * Federal comment periods close at 11:59 PM Eastern on their
 * `commentsCloseOn` date (Regulations.gov states every deadline that way).
 * Whether a period is open is therefore a question about the calendar date
 * in Eastern time — not UTC, which shut every period 4-5 hours early, and
 * not the reader's own zone, which kept it open for a Pacific reader after
 * the backend had started refusing the submission. The backend asks the
 * same question (`comment_period_today` in backend/app/time_utils.py).
 */
const COMMENT_DEADLINE_TZ = "America/New_York";

/** Today's date in the comment-deadline zone, as `YYYY-MM-DD`. */
export function commentPeriodToday(now: number | Date = Date.now()): string {
  // Assembled from parts rather than trusting a locale's layout (en-CA's
  // YYYY-MM-DD): a runtime without that locale's data falls back to another
  // layout silently, and the result is compared as a string.
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: COMMENT_DEADLINE_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** True while a period closing on `closeDate` still accepts comments. */
export function isCommentPeriodOpen(
  closeDate: string | null | undefined,
  now: number | Date = Date.now()
): boolean {
  return !!closeDate && closeDate >= commentPeriodToday(now);
}

/** Whole days until a period closes: 0 on the closing day itself (and
 * after it), 1 the day before. */
export function commentDaysLeft(closeDate: string, now: number | Date = Date.now()): number {
  const close = Date.parse(`${closeDate}T00:00:00Z`);
  const today = Date.parse(`${commentPeriodToday(now)}T00:00:00Z`);
  if (Number.isNaN(close) || Number.isNaN(today)) return 0;
  return Math.max(0, Math.round((close - today) / 86_400_000));
}

/**
 * Days remaining until a comment period closes, phrased for a reader.
 *
 * `asOf` is passed in rather than read from the clock: a countdown computed
 * during render would change without any input changing, which is both impure
 * and untestable. Callers read the clock once, when the deadline arrives.
 */
export function describeDaysLeft(closeDate: string, asOf: number): string {
  if (Number.isNaN(Date.parse(`${closeDate}T00:00:00Z`))) return "";
  const diff = commentDaysLeft(closeDate, asOf);
  if (diff <= 0) return "closes today";
  if (diff === 1) return "1 day left";
  return `${diff} days left`;
}

/**
 * An overall score as it is shown everywhere: a whole number. Sub-scores are
 * integers; the decimals an overall picks up from the weights (.33/.33/.34)
 * carry no information, and showing them ranked members apart on differences
 * no measurement here can resolve. Ranks and ties are taken on this value.
 */
export function displayScore(score: number): number {
  return Math.round(score);
}

/**
 * Standard competition ranks ("1224") for a list already in display order:
 * equal keys share the rank of the first of them, and the next distinct key
 * skips past the tie. Numbering by position gave two members with the same
 * score different ranks, decided by the alphabetical tiebreak.
 */
export function competitionRanks<T>(
  items: readonly T[],
  key: (item: T) => unknown,
  offset = 0
): number[] {
  const ranks: number[] = [];
  items.forEach((item, i) => {
    ranks.push(i > 0 && key(item) === key(items[i - 1]) ? ranks[i - 1] : offset + i + 1);
  });
  return ranks;
}

/**
 * A backend phrase shown on its own as a label: its first letter raised
 * ("progressive Democrat leader" -> "Progressive Democrat leader"). The
 * phrase is written lowercase so it also reads mid-sentence.
 */
export function asLabel(phrase: string): string {
  return phrase.charAt(0).toUpperCase() + phrase.slice(1);
}

/**
 * A policy-area code as a reader sees it ("FOREIGN_POLICY" -> "FOREIGN
 * POLICY"). The codes come from config_definitions.POLICY_AREAS; every
 * surface that prints one sets it in uppercase mono, so only the
 * underscore needs to go.
 */
export function policyAreaLabel(code: string): string {
  return code.replace(/_/g, " ");
}

/**
 * Whether an issue's summary only repeats its headline. When a source gives
 * a headline and no other attributable sentence, the summary is that
 * headline again (backend post_composer), and a card that prints both reads
 * as a glitch. Compared as letters and digits, so "Headline." and "headline"
 * match, and a summary that is the headline cut short matches too.
 */
export function restatesTitle(title: string, summary: string | null | undefined): boolean {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "");
  const s = norm(summary ?? "");
  return s.length > 0 && norm(title).includes(s);
}
