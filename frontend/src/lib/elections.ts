/** Shared display helpers for the midterm-elections feature (race labels,
 * PVI formatting, UTC parsing, sorting). Pure functions only — safe to
 * import from both server and client components.
 */

import type { BallotCandidate, CandidateSummary } from "@/types/election";
import { formatCurrency } from "@/lib/formatting";

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

/** "TN-2", "AK-AL": a House seat's short label. Never `${state}-${district}`,
 *  which reads "AK-0" for an at-large seat. */
export function houseSeatLabel(state: string, district: number): string {
  return `${state}-${districtToken(district)}`;
}

/** "District 2", "At-large district": a House seat named on its own. */
export function districtName(district: number): string {
  return district === 0 ? "At-large district" : `District ${district}`;
}

/** A race's short label without the state — "SENATE" / "HOUSE-7" /
 * "HOUSE-AL" — for badges inside a page already scoped to one state (e.g. the state ballot's aggregated coverage feed), where
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
 * live in, a candidate's name (their representative's, when that member
 * is running again — a retiring member is on no row, and in a state that
 * redrew for this cycle the member's name finds the candidate's NEW
 * district, not necessarily the reader's), and the district number if
 * they happen to know it. Civitas never asks for
 * a street address, so the filter has to work from what a person can
 * recall unprompted — see the House section's own copy.
 *
 * `areas` is whatever place names that district is described by — the
 * counties on a U.S. House row, the towns on a state legislative one.
 * Matching runs against that list IN FULL, not the truncated
 * districtAreaLabel display string: a reader typing "washington"
 * must still match a district whose label elided it behind "& 2 more".
 * Substring, compared through searchFold on both sides.
 */
export function matchesDistrictQuery(
  race: {
    /** A U.S. House district is a number (0 = at-large); a state
     * legislative one is a string, because "10A" and "10B" are real. */
    district: number | string | null;
    areas: string[] | null;
    candidates: { name: string; ballotName?: string | null }[];
  },
  query: string
): boolean {
  const raw = query.trim().toLowerCase();
  const q = searchFold(query);
  if (!raw) return true;
  // "AL" is what an at-large district renders as, so it must also be
  // what an at-large district is searchable by.
  const districtLabel = race.district === 0 ? "al" : String(race.district ?? "").toLowerCase();
  // A multi-member district renders as "1a"/"1b" (Idaho) or "5-1"/"5-2"
  // (Washington), but a voter there knows they are in district 1 — and
  // typing it must not come back empty. The numeric part matches both
  // seats; "1" still does not match "10", because "10" leads with "10".
  const districtNumber = districtLabel.match(/^\d+/)?.[0] ?? "";
  return (
    districtLabel === raw ||
    (districtNumber !== "" && districtNumber !== districtLabel && districtNumber === raw) ||
    (q !== "" && (race.areas ?? []).some((a) => searchFold(a).includes(q))) ||
    race.candidates.some(
      (c) =>
        q !== "" && (searchFold(c.name).includes(q) || searchFold(c.ballotName ?? "").includes(q))
    )
  );
}

/** A place or person's name reduced to what a reader types: no case, no
 * accents ("Dona Ana" finds "Doña Ana County"), no punctuation ("prince
 * georges" and a phone's curly "Prince George’s" both find "Prince
 * George's"; "st louis" finds "St. Louis"), and "saint" read as "st". */
export function searchFold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[.'\u2018\u2019]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\bsaint\b/g, "st")
    .trim();
}

/** What a candidate's money column says: the FEC's contributions for this
 * election, or in words why there is no figure. Never a fabricated $0 —
 * someone the state lists who never filed, someone not synced yet, and
 * someone with no FEC report for this election each say so. Read from the
 * figure, not `hasRaisedFunds`: the FEC's roster flag can say no money
 * while its totals report some (12 such candidates on 2026-10-08). */
export function raisedLabel(c: BallotCandidate): string {
  if (c.fecFiled === false) return "no FEC filing";
  if (c.lastFinancialsSync == null) return "awaiting FEC sync";
  if (!c.contributions) return "no funds reported";
  return formatCurrency(c.contributions);
}

/** A candidate's name as the state prints it on its ballot, else the
 * FEC's. The one place a page picks between them. */
export function candidateName(c: { name: string; ballotName?: string | null }): string {
  return c.ballotName || c.name;
}

/** "4-year terms" — an office's regular term, worded for the office rather
 * than the winner, since a seat filled for the rest of an unexpired term
 * runs shorter. Null when the backend has no term for it. */
export function termPhrase(years: number | null | undefined): string | null {
  return years ? `${years}-year terms` : null;
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
 *
 * A candidate the state has confirmed is on the ballot is active whatever
 * their FEC record says: North Carolina's certified Libertarian for Senate
 * raised nothing and is not a statutory candidate, and was hidden under
 * "other filers" on a ballot she is printed on.
 */
export function isActiveCandidate(c: CandidateSummary): boolean {
  return (
    c.confirmed || c.candidateStatus === "C" || c.hasRaisedFunds || c.incumbentChallenge === "I"
  );
}

/** FEC's incumbency codes, in words. */
const FEC_INCUMBENCY: Record<string, string> = {
  I: "INCUMBENT",
  C: "CHALLENGER",
  O: "OPEN SEAT",
};

/** Whether `race` is a House seat on lines other than the ones its
 * members were elected on — the members going into this election, or the
 * ones sitting now (StateBallot.newDistrictLines). Senate seats are
 * statewide and never redrawn. */
export function isRedrawnSeat(
  race: { office: string },
  newDistrictLines: boolean | undefined
): boolean {
  return !!newDistrictLines && race.office === "H";
}

/** A candidate's FEC incumbency code as the page says it, or null to say
 * nothing.
 *
 * On a redrawn seat (isRedrawnSeat) a district number names a different
 * place than the one the member going into the election was elected in:
 * a member who held one numbered seat can run in a differently numbered one
 * on the new map. "Incumbent" there
 * claims a seat nobody holds — the page itself says no seat on the new
 * lines has a previous holder — so "I" names the person, not the
 * district, and CHALLENGER / OPEN SEAT, which describe the old seat, are
 * not said at all. Where the payload names the seat the member held going
 * in (incumbentRecord.seat), it is said too.
 *
 * How "I" is worded depends on `resultsMode` (from election day on):
 * before it the member is sitting — SITTING MEMBER, TX-35 — but the
 * results window can run to January 3, when the Congress this election
 * seated takes office, and from noon that day a defeated member no longer
 * sits and a re-elected one sits for the new seat. So in results mode it
 * reads MEMBER BEFORE THIS ELECTION, TX-35, true on any day of the
 * window, the same framing as the district drawer's "your representative
 * going into this election". It names a time, not a direction: "member
 * going in" beside a live count read as headed into the seat, which is a
 * step from calling the race. redrawnMemberWords is that wording alone, for a page that
 * sets it in its own case. */
export function redrawnMemberWords(resultsMode: boolean): string {
  return resultsMode ? "MEMBER BEFORE THIS ELECTION" : "SITTING MEMBER";
}

export function incumbencyLabel(
  code: string | null,
  redrawnSeat: boolean,
  heldSeat?: string | null,
  resultsMode = false
): string | null {
  if (!code) return null;
  if (redrawnSeat) {
    if (code !== "I") return null;
    const member = redrawnMemberWords(resultsMode);
    return heldSeat ? `${member}, ${heldSeat}` : member;
  }
  return FEC_INCUMBENCY[code] ?? code;
}

/** Display suffixes for FEC codes that are a Democratic state affiliate —
 * DFL (Minnesota), D-NPL (North Dakota, FEC code DNL) — for
 * CandidateCard.tsx's PARTY_META label ("DEMOCRAT (DFL)"). Labels only:
 * WHICH codes are the same party comes from the backend, as each
 * candidate's `partyGroup` (FEC_PARTY_ALIASES), so the page keeps no
 * second list of it. */
export const DEM_AFFILIATE_PARTIES: Record<string, string> = { DFL: "DFL", DNL: "D-NPL" };

/** Which major party a candidate belongs to, or null for anyone else —
 * read from the backend's `partyGroup` (a DFL nominee's is "DEM"), so a
 * real DFL/DNL nominee reads as the major-party candidate everywhere on
 * the page. A statewide nominee's `party` is already that group. */
export function majorPartyOf(c: {
  party: string;
  partyGroup?: string | null;
}): "DEM" | "REP" | null {
  const group = c.partyGroup ?? c.party;
  if (group === "DEM") return "DEM";
  if (group === "REP") return "REP";
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
    active.filter((c) => majorPartyOf(c) === party).sort((a, b) => byRaised(b) - byRaised(a))[0] ??
    null;
  const majorLeaders = [topOf("DEM"), topOf("REP")].filter((c): c is BallotCandidate => c != null);
  // Negative cash on hand floors at 0 rather than going negative:
  // a leader below zero still means "no real minor-party threat", not "any
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
    .filter((c) => majorPartyOf(c) == null && !leaderIds.has(c.id))
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
