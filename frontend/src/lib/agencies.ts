import { formatCurrency, formatUtcDate } from "@/lib/formatting";

/** A dollar figure USAspending reported, or "not reported": a figure it
 * hasn't reported is never shown as $0. */
export function money(v: number | null | undefined): string {
  return v == null ? "not reported" : formatCurrency(v);
}

/** "Oct 15, 2026" for a YYYY-MM-DD the backend says it read something on. */
export function readOn(iso: string | null): string {
  return iso
    ? formatUtcDate(iso, { year: "numeric", month: "short", day: "numeric" }, "en-US")
    : "not yet";
}

// Fiscal months: the federal fiscal year starts October 1.
const FISCAL_MONTHS = [
  "October",
  "November",
  "December",
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
];

/** "FY2025" for a whole fiscal year, "FY2026 through August" for one whose
 * figures stop short of its twelfth month (USAspending's reporting period). */
export function fiscalYearLabel(fy: number | null, throughPeriod: number | null): string {
  if (!fy) return "";
  return throughPeriod && throughPeriod < 12
    ? `FY${fy} through ${FISCAL_MONTHS[throughPeriod - 1]}`
    : `FY${fy}`;
}

function words(s: string): string[] {
  return s
    .toLowerCase()
    .replace(/&/g, " and ")
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/** The agencies a name search finds, best first: its short name exactly
 * ("EPA", "IRS"), then its name exactly, then every typed word starting a
 * word of its name or short name ("forest" finds the Forest Service). A
 * match inside a word does not count: "epa" is in "Department", and a
 * substring search put the EPA sixth of 28. Among word matches the closer
 * match leads: more typed words matched whole, then the larger share of the
 * name the typed words make up ("education" puts the Education Department,
 * half its name, ahead of a foundation whose eight-word name merely
 * contains it; alphabetical order had it third). Ties keep the given order. */
export function matchAgencies<T extends { name: string; shortName: string | null }>(
  agencies: T[],
  q: string
): T[] {
  const typed = words(q);
  if (!typed.length) return [];
  const whole = typed.join(" ");
  const score = (a: T): [number, number, number] | null => {
    const short = words(a.shortName ?? "").join(" ");
    if (short && short === whole) return [0, 0, 0];
    const name = words(a.name);
    if (name.join(" ") === whole) return [1, 0, 0];
    const pool = [...name, ...words(a.shortName ?? "")];
    if (!typed.every((t) => pool.some((w) => w.startsWith(t)))) return null;
    const exact = typed.filter((t) => pool.includes(t)).length;
    const content = name.filter((w) => !FILLER.has(w));
    const covered = content.filter((w) => typed.some((t) => w.startsWith(t))).length;
    return [2, -exact, -covered / Math.max(content.length, 1)];
  };
  return agencies
    .map((a, i) => ({ a, i, s: score(a) }))
    .filter((x): x is { a: T; i: number; s: [number, number, number] } => x.s !== null)
    .sort((x, y) => x.s[0] - y.s[0] || x.s[1] - y.s[1] || x.s[2] - y.s[2] || x.i - y.i)
    .map((x) => x.a);
}

// Words that don't make a name longer for the share above.
const FILLER = new Set(["of", "the", "and", "for", "on", "in"]);
