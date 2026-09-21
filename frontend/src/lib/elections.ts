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

/** Same idea as raceTitleLabel but without the state prefix — "SENATE" /
 * "HOUSE-7" / "HOUSE-AL" — for badges inside a page already scoped to
 * one state (e.g. the state ballot's aggregated coverage feed), where
 * repeating the state on every item would be redundant. */
export function raceBadgeLabel(race: { office: string; district: number | null }): string {
  if (race.office === "S") return "SENATE";
  if (race.district == null) return "HOUSE";
  return `HOUSE-${districtToken(race.district)}`;
}

/** "Rockdale, Newton, DeKalb (part) & 2 more" — a short, scannable hint
 * for a district picker, for a voter who knows the place they live but
 * not their district number. Counties for a U.S. House seat, towns for a
 * state legislative one.
 *
 * TRUNCATION IS THE POINT, and it is why matchesDistrictQuery searches
 * the full list rather than this string: a rural Minnesota senate
 * district covers 292 townships, and rendering all of them would bury
 * the row — but a reader in the 290th still has to be able to find their
 * seat by typing its name.
 *
 * Drops the generic " County" suffix (kept for Louisiana's
 * "Parish"/Alaska's "Borough"/Virginia's "city" etc., which carry real
 * information) — but ONLY where every entry is a county, which is why
 * `dropCountySuffix` exists. A state legislative row lists places, with
 * a county appearing only as the fallback for a district that contains
 * no incorporated place: there, stripping the suffix turns
 * "Forsyth County" into "Forsyth", which is a different real Georgia
 * place (Forsyth city) sitting in the very same list.
 *
 * "(part)" is left as-is since it means that county is split across
 * districts. Null in, null out — a district missing from the crosswalk
 * stays unlabeled, never a guessed list. */
/* parseUtc moved to lib/formatting.ts — it is a generic ISO-8601 concern,
   and the records band and homepage index need it too. Re-exported here so
   existing election call sites keep their import path. */
export { parseUtc } from "./formatting";

export function districtAreaLabel(
  areas: string[] | null,
  max = 3,
  dropCountySuffix = true
): string | null {
  if (!areas || areas.length === 0) return null;
  const short = dropCountySuffix ? areas.map((a) => a.replace(/ County\b/, "")) : areas.slice();
  if (short.length <= max) return short.join(", ");
  return `${short.slice(0, max).join(", ")} & ${short.length - max} more`;
}

/** Does this district row match what the reader typed in the district
 * filter?
 *
 * Deliberately matches on the three things a reader plausibly knows
 * about themselves without being asked for an address: the county they
 * live in, their sitting representative's name (or any candidate's), and
 * the district number if they happen to know it. Civitas never asks for
 * a street address, so the filter has to work from what a person can
 * recall unprompted — see the House section's own copy.
 *
 * `areas` is whatever place names that district is described by — the
 * counties on a U.S. House row, the towns on a state legislative one.
 * Matching runs against that list IN FULL, not the truncated
 * districtAreaLabel display string: a reader typing "washington"
 * must still match a district whose label elided it behind "& 2 more".
 * Substring, case-insensitive.
 */
export function matchesDistrictQuery(
  race: {
    /** A U.S. House district is a number (0 = at-large); a state
     * legislative one is a string, because "10A" and "10B" are real. */
    district: number | string | null;
    areas: string[] | null;
    candidates: { name: string }[];
  },
  query: string
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  // "AL" is what an at-large district renders as, so it must also be
  // what an at-large district is searchable by.
  const districtLabel =
    race.district === 0 ? "al" : String(race.district ?? "").toLowerCase();
  // A multi-member district renders as "1a"/"1b" (Idaho) or "5-1"/"5-2"
  // (Washington), but a voter there knows they are in district 1 — and
  // typing it must not come back empty. The numeric part matches both
  // seats; "1" still does not match "10", because "10" leads with "10".
  const districtNumber = districtLabel.match(/^\d+/)?.[0] ?? "";
  return (
    districtLabel === q ||
    (districtNumber !== "" && districtNumber !== districtLabel && districtNumber === q) ||
    (race.areas ?? []).some((a) => a.toLowerCase().includes(q)) ||
    race.candidates.some((c) => c.name.toLowerCase().includes(q))
  );
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

/** FEC party codes that are the Democratic Party's state-level
 * affiliates — DFL (Minnesota), D-NPL (North Dakota, FEC code DNL) —
 * mapped to the display suffix CandidateCard.tsx's PARTY_META uses in
 * its label ("DEMOCRAT (DFL)"). The single source of truth for "which
 * codes count as Democratic": majorPartyOf and PARTY_META both read
 * from this list instead of each keeping an independent copy, which
 * previously meant a new affiliate added to one could silently miss
 * the other. */
export const DEM_AFFILIATE_PARTIES: Record<string, string> = { DFL: "DFL", DNL: "D-NPL" };

/** Which major party a candidate's FEC code belongs to, or null for
 * anyone else — so a real DFL/DNL nominee reads as the major-party
 * candidate everywhere on the page, not just on their own card. */
export function majorPartyOf(party: string): "DEM" | "REP" | null {
  if (party === "DEM" || party in DEM_AFFILIATE_PARTIES) return "DEM";
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
  // Money raised THIS cycle, not cash on hand: cash on hand can be a
  // carryover balance sitting in a committee that was never wound down
  // (an incumbent who announced they aren't running again keeps a real,
  // often large, cash balance with no current campaign behind it) --
  // contributions can't be inflated that way, since they're scoped to
  // the current election cycle specifically.
  const byRaised = (c: BallotCandidate) => c.contributions ?? 0;

  const topOf = (party: "DEM" | "REP") =>
    active.filter((c) => majorPartyOf(c.party) === party).sort((a, b) => byRaised(b) - byRaised(a))[0] ??
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

  // Neither major party has filed yet and nobody's an incumbent (a real
  // shape: an early-cycle district where only third-party/independent
  // candidates have filed FEC paperwork so far) -- the checks above
  // never promote anyone, since bestOther's threshold requires a major
  // leader to compare against. Falling through to an empty leader set
  // would render NO cards and NO financials chart for a race that does
  // have real, active candidates. Show the top fundraiser overall
  // instead, so a real race is never a blank one.
  if (leaderIds.size === 0 && active.length > 0) {
    const topOverall = [...active].sort((a, b) => byCash(b) - byCash(a))[0];
    leaderIds.add(topOverall.id);
  }

  const leaders: BallotCandidate[] = [];
  const tail: BallotCandidate[] = [];
  for (const c of active) {
    (leaderIds.has(c.id) ? leaders : tail).push(c);
  }
  return { leaders, tail };
}
