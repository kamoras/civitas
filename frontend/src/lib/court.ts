import type { Court, CourtJustice, CourtPair } from "@/types/court";

/** A split as the Court's tally reads it: "6-3" from the API is "6–3". */
export function splitLabel(split: string): string {
  return split.replace("-", "–");
}

/** The terms a record covers, in words: "the 2025 term", "the 2023 to 2025
 *  terms" (consecutive), "the 2022 and 2025 terms", "the 2021, 2023 and
 *  2025 terms". Never "the last four terms": a window can include a term
 *  with no decisions yet. */
export function termsPhrase(terms: number[]): string {
  const t = [...new Set(terms)].sort((a, b) => a - b);
  if (t.length === 0) return "";
  if (t.length === 1) return `the ${t[0]} term`;
  const consecutive = t.every((y, i) => i === 0 || y === t[i - 1] + 1);
  if (consecutive) return `the ${t[0]} to ${t[t.length - 1]} terms`;
  return `the ${t.slice(0, -1).join(", ")} and ${t[t.length - 1]} terms`;
}

/** A Martin-Quinn position, signed with a true minus: "+0.52", "−1.84". */
export function signedPosition(x: number): string {
  return `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}`;
}

/** The surname a lineup and the heatmap label a justice by: the API's
 *  last name without a suffix ("Alito, Jr." is "Alito"). */
export function surname(j: Pick<CourtJustice, "lastName" | "name">): string {
  const last = (j.lastName || j.name).split(",")[0].trim();
  return last || j.name;
}

/** Each pair once, looked up either way round. */
export function pairLookup(pairs: CourtPair[]): (a: string, b: string) => CourtPair | null {
  const map = new Map<string, CourtPair>();
  for (const p of pairs) {
    map.set(`${p.a}|${p.b}`, p);
    map.set(`${p.b}|${p.a}`, p);
  }
  return (a, b) => map.get(`${a}|${b}`) ?? null;
}

/** The heatmap's scale: from the lowest share shown, rounded down to ten,
 *  to 100. A fixed floor would spend most of the ramp on shares no pair
 *  has. */
export function heatDomain(pairs: CourtPair[]): [number, number] {
  if (pairs.length === 0) return [0, 100];
  const low = Math.min(...pairs.map((p) => p.share));
  return [Math.min(90, Math.floor(low / 10) * 10), 100];
}

// The ramp's ends: the page's raised surface, and a dark teal on which the
// heading ink (#F2EEE7) still reads at 6.4:1, so every cell's value label
// passes WCAG AA whatever its shade (court.test.ts checks both ends).
export const HEAT_LOW = [0x19, 0x15, 0x12] as const;
export const HEAT_HIGH = [0x1f, 0x5e, 0x60] as const;

/** A cell's background for `share` on the domain: a straight line between
 *  the ramp's ends. Darker is more agreement; the value label carries the
 *  figure itself, so colour is never the only encoding. */
export function heatColor(share: number, [lo, hi]: [number, number]): string {
  const t = hi > lo ? Math.max(0, Math.min(1, (share - lo) / (hi - lo))) : 1;
  const c = HEAT_LOW.map((l, i) => Math.round(l + (HEAT_HIGH[i] - l) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/** Where a position falls on an axis from `lo` to `hi`, as a percentage. */
export function axisAt(x: number, lo: number, hi: number): number {
  return hi > lo ? ((Math.max(lo, Math.min(hi, x)) - lo) / (hi - lo)) * 100 : 50;
}

/** The line's axis: every estimate and interval, padded to whole numbers
 *  and always including 0, so the left-right midpoint of the scale shows. */
export function positionDomain(justices: CourtJustice[]): [number, number] {
  const xs = justices.flatMap((j) =>
    j.mq ? [j.mq.position, j.mq.low ?? j.mq.position, j.mq.high ?? j.mq.position] : []
  );
  if (xs.length === 0) return [-1, 1];
  return [Math.min(0, Math.floor(Math.min(...xs))), Math.max(0, Math.ceil(Math.max(...xs)))];
}

/** The Court page's address, for the section table and every link. */
export const COURT_HREF = "/court";

/** What every figure on the Court page is read from. */
export const COURT_SOURCES = "Supreme Court Database; Oyez; Martin-Quinn scores";

/** The page's lead, from the API's counts: the share of decisions that
 *  were unanimous (most readers expect the Court to split on most cases),
 *  and how many turned on one vote. Null with nothing stored. */
export function courtLead(
  court: Pick<Court, "record">
): { figure: string; headline: string; detail: string } | null {
  const r = court.record;
  if (r.cases === 0) return null;
  const terms = termsPhrase(r.terms);
  return {
    figure: `${r.unanimousPct}%`,
    headline: `of the Court's decisions in ${terms} were unanimous.`,
    detail: `${r.unanimous} of ${r.cases} orally argued cases had no dissent. ${r.oneVote} ${
      r.oneVote === 1 ? "was" : "were"
    } decided by a single vote (${r.oneVotePct}%).`,
  };
}

/** Where the current term stands, so an empty new term reads as what it
 *  is: the term has begun and nothing is decided yet. */
export function currentTermLine(court: Pick<Court, "currentTerm" | "record">): string {
  const { term, decided } = court.currentTerm;
  if (decided > 0)
    return `The ${term} term: ${decided} ${decided === 1 ? "decision" : "decisions"} so far.`;
  const last = court.record.terms[court.record.terms.length - 1];
  return last
    ? `The ${term} term has no decision on record yet; the latest decisions are from the ${last} term.`
    : `The ${term} term has no decision on record yet.`;
}
