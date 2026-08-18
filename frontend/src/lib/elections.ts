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

/** Same idea as raceShortLabel but without the state prefix — "SENATE" /
 * "HOUSE-7" / "HOUSE-AL" — for badges inside a page already scoped to
 * one state (e.g. the state ballot's aggregated coverage feed), where
 * repeating the state on every item would be redundant. */
export function raceBadgeLabel(race: { office: string; district: number | null }): string {
  if (race.office === "S") return "SENATE";
  if (race.district == null) return "HOUSE";
  return `HOUSE-${districtToken(race.district)}`;
}

/** "Rockdale, Newton, DeKalb (part) & 2 more" — a short, scannable hint
 * for a district picker, for a voter who knows their county but not
 * their district number. Drops the generic " County" suffix (kept for
 * Louisiana's "Parish"/Alaska's "Borough"/Virginia's "city" etc., which
 * carry real information); "(part)" is left as-is since it means that
 * county is split across districts. Null in, null out — a district
 * missing from the crosswalk stays unlabeled, never a guessed list. */
/* parseUtc moved to lib/formatting.ts — it is a generic ISO-8601 concern,
   and the records band and homepage index need it too. Re-exported here so
   existing election call sites keep their import path. */
export { parseUtc } from "./formatting";

export function districtCountiesLabel(counties: string[] | null, max = 3): string | null {
  if (!counties || counties.length === 0) return null;
  const short = counties.map((c) => c.replace(/ County\b/, ""));
  if (short.length <= max) return short.join(", ");
  return `${short.slice(0, max).join(", ")} & ${short.length - max} more`;
}

/** "Active" candidates get full card treatment; the rest (paper filers,
 * prior-cycle FEC records) are collapsed under "OTHER FEC FILERS" and
 * excluded from the fundraising bars. FEC "C" = statutory candidate.
 */
export function isActiveCandidate(c: CandidateSummary): boolean {
  return c.candidateStatus === "C" || c.hasRaisedFunds || c.incumbentChallenge === "I";
}
