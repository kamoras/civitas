/** Shared display helpers for the midterm-elections feature (race labels,
 * PVI formatting, UTC parsing, sorting). Pure functions only — safe to
 * import from both server and client components.
 */

import type { CandidateSummary } from "@/types/election";

/** The minimal race shape the label/sort helpers need — lets tests and
 * callers pass either RaceSummary or RaceDetail. */
export interface RaceLike {
  /** "S" = Senate, "H" = House — FEC office codes. */
  office: string;
  state: string;
  /** null = statewide (Senate); 0 = at-large House district (FEC "00"). */
  district: number | null;
}

/** Formats a signed PVI int as "R+3"/"D+3"/"EVEN" — display-only, not a computation. */
export function formatPvi(pvi: number | null): string {
  if (pvi == null) return "N/A";
  if (pvi === 0) return "EVEN";
  return pvi > 0 ? `R+${pvi}` : `D+${Math.abs(pvi)}`;
}

export function pviColor(pvi: number | null): string {
  if (pvi == null) return "text-matrix-green/40";
  if (pvi === 0) return "text-white/60";
  return pvi > 0 ? "text-rep-red" : "text-dem-blue";
}

/** District number for labels: 0 (FEC "00", at-large) renders as "AL".
 * Callers must have already ruled out null (Senate). */
function districtToken(district: number): string {
  return district === 0 ? "AL" : String(district);
}

/** Card-style label in caps: "GA SENATE" / "GA-7" / "AK-AL". */
export function raceShortLabel(race: RaceLike): string {
  if (race.office === "S") return `${race.state} SENATE`;
  if (race.district == null) return `${race.state} HOUSE`;
  return `${race.state}-${districtToken(race.district)}`;
}

/** Title-style label: "GA Senate" / "GA-7 House" / "AK-AL House". */
export function raceTitleLabel(race: RaceLike): string {
  if (race.office === "S") return `${race.state} Senate`;
  if (race.district == null) return `${race.state} House`;
  return `${race.state}-${districtToken(race.district)} House`;
}

/** Race-grid sort: state A→Z, Senate before House within a state, then
 * district ascending (at-large 0 first; Senate's null district never
 * competes with House numbers because office sorts first). */
export function compareRaces(a: RaceLike, b: RaceLike): number {
  if (a.state !== b.state) return a.state < b.state ? -1 : 1;
  if (a.office !== b.office) return a.office === "S" ? -1 : 1;
  return (a.district ?? -1) - (b.district ?? -1);
}

/** Parses an ISO-8601 timestamp, treating an offset-less string as UTC —
 * `new Date("2026-07-04T12:00:00")` would otherwise parse as viewer-local
 * time (repo precedent: admin/page.tsx's `new Date(startIso + "Z")`).
 * Returns null for unparseable input.
 */
export function parseUtc(iso: string): Date | null {
  const hasTime = /[T ]\d{2}:\d{2}/.test(iso);
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasTime && !hasOffset ? `${iso}Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Active" candidates get full card treatment; the rest (paper filers,
 * prior-cycle FEC records) are collapsed under "OTHER FEC FILERS" and
 * excluded from the fundraising bars. FEC "C" = statutory candidate.
 */
export function isActiveCandidate(c: CandidateSummary): boolean {
  return c.candidateStatus === "C" || c.hasRaisedFunds || c.incumbentChallenge === "I";
}
