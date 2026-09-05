/** Shared display helpers for the midterm-elections feature (race labels,
 * PVI formatting, UTC parsing, sorting). Pure functions only — safe to
 * import from both server and client components.
 */

import type { BallotCandidate, CandidateSummary } from "@/types/election";

/** The minimal race shape the label/sort helpers need — lets tests and
 * callers pass any of the race types (RaceSummary, RaceWithCandidates)
 * without depending on their full shape. */
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

/** Solid palette hex, so a lean figure never renders below the contrast
 * floor. One function, not two: this used to also exist as pviTextColor,
 * copy-pasted into the elections hub while this file's own copy served
 * the race page — byte-identical bodies, which is exactly how two copies
 * drift apart unnoticed. */
export function pviColor(pvi: number | null): string {
  if (pvi == null) return "text-ink-min";
  if (pvi === 0) return "text-ink";
  return pvi > 0 ? "text-signal-red" : "text-dem-blue";
}

/** District number for labels: 0 (FEC "00", at-large) renders as "AL".
 * Callers must have already ruled out null (Senate). */
function districtToken(district: number): string {
  return district === 0 ? "AL" : String(district);
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

/** Canonical href for a state's ballot page.
 *
 * Plural "states" deliberately, matching the API path
 * (/api/elections/states/{ST}) — and note that the singular
 * /elections/state would be swallowed by the sibling [raceId] dynamic
 * segment and 404 as an unknown race, so the two spellings are not
 * interchangeable here. Named rather than inlined for the reason
 * ACTION_CENTER_HREF is (see lib/routes.ts): a URL shape with a
 * non-obvious constraint attracts well-meaning "cleanup".
 */
export function stateBallotHref(state: string): string {
  return `/elections/states/${encodeURIComponent(state.toUpperCase())}`;
}

/** Human label for a measure's status. `removed` is rendered, never
 * hidden: a voter who saw a measure last week needs to be told a court
 * struck it, and an absent card cannot say that. */
export function measureStatusLabel(status: string): string {
  switch (status) {
    case "removed":
      return "REMOVED FROM BALLOT";
    case "withdrawn":
      return "WITHDRAWN";
    case "under_appeal":
      return "UNDER APPEAL";
    default:
      return "ON THE BALLOT";
  }
}

/** "Active" candidates get full card treatment; the rest (paper filers,
 * prior-cycle FEC records) are collapsed under "OTHER FEC FILERS" and
 * excluded from the fundraising bars. FEC "C" = statutory candidate.
 */
export function isActiveCandidate(c: CandidateSummary): boolean {
  return c.candidateStatus === "C" || c.hasRaisedFunds || c.incumbentChallenge === "I";
}

/** Which major party a candidate's FEC code belongs to, or null for
 * anyone else. DFL (Minnesota)/DNL (North Dakota) are the Democratic
 * Party's state-level affiliate names on the FEC's own party-code list —
 * same rule CandidateCard.tsx's PARTY_META already applies for color and
 * label, so a real DFL/DNL nominee reads as the major-party candidate
 * everywhere on the page, not just on their own card. */
export function majorPartyOf(party: string): "DEM" | "REP" | null {
  if (party === "DEM" || party === "DFL" || party === "DNL") return "DEM";
  if (party === "REP") return "REP";
  return null;
}

export interface RaceTiers {
  /** Gets a full CandidateCard: the top fundraiser in each major party,
   * every incumbent regardless of party or amount, and at most one real
   * third-party/independent contender. */
  leaders: BallotCandidate[];
  /** Everyone else active — real filers, just not shown as if they were
   * equally likely to be on the ballot. */
  tail: BallotCandidate[];
}

/** Splits an unfiltered ("filers"/"primary" candidateSource) race into
 * who's actually likely contending and who's merely filed, by LAYOUT
 * rather than a disclaimer: a leader gets the same full card TX's
 * already-narrowed races use, everyone else recedes into a compact row.
 * A "confirmed"/"nominees" race is already a real, small list and never
 * needs this — callers only run it on the two source values it's for.
 *
 * Deliberately a fundraising-based heuristic, not a guess at who will
 * win: a major party's own top fundraiser is shown even at $0 (an empty
 * or uncontested side reads as exactly that — fewer cards — rather than
 * an invented opponent), and a non-major-party candidate only joins the
 * leader row when their cash is a real fraction of the major-party
 * leaders', so a $26 independent in a $16M Senate race doesn't get the
 * same visual weight as the actual contest.
 */
export function tierCandidates(candidates: BallotCandidate[]): RaceTiers {
  const active = candidates.filter(isActiveCandidate);
  const byCash = (c: BallotCandidate) => c.cashOnHand ?? 0;

  const topOf = (party: "DEM" | "REP") =>
    active.filter((c) => majorPartyOf(c.party) === party).sort((a, b) => byCash(b) - byCash(a))[0] ??
    null;
  const majorLeaders = [topOf("DEM"), topOf("REP")].filter(
    (c): c is BallotCandidate => c != null,
  );
  // Debt (negative cash on hand) floors at 0 rather than going negative:
  // a leader in debt still means "no real minor-party threat", not "any
  // non-negative minor candidate counts as one" (the >0 guard below).
  const bestMajorCash = Math.max(0, ...majorLeaders.map(byCash));

  const leaderIds = new Set<string>(majorLeaders.map((c) => c.id));
  for (const c of active) {
    if (c.incumbentChallenge === "I") leaderIds.add(c.id);
  }
  // 10% of the stronger major-party leader's cash is a small, named-once
  // bar for "this minor-party/independent run looks real" — not re-tuned
  // per race, and not meant to predict who wins, just who's worth a card.
  const bestOther = active
    .filter((c) => majorPartyOf(c.party) == null && !leaderIds.has(c.id))
    .sort((a, b) => byCash(b) - byCash(a))[0];
  if (bestOther && bestMajorCash > 0 && byCash(bestOther) >= bestMajorCash * 0.1) {
    leaderIds.add(bestOther.id);
  }

  const leaders: BallotCandidate[] = [];
  const tail: BallotCandidate[] = [];
  for (const c of active) {
    (leaderIds.has(c.id) ? leaders : tail).push(c);
  }
  return { leaders, tail };
}
