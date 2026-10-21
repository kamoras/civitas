/** Shared display helpers for the elections feature (race labels,
 * PVI formatting, UTC parsing, sorting). Pure functions only — safe to
 * import from both server and client components.
 */

import type { BallotBasis, BallotCandidate, CandidateSummary } from "@/types/election";
import { formatCurrency } from "@/lib/formatting";

/** Which page title the elections hub is showing. */
export type ElectionTitleView = "election_day" | "results" | "ballot" | "elections";

/** The elections hub's H1 for a cycle: "2026 midterm ballot", "2028
 * presidential election results". The word comes from the cycle year, never
 * a literal: a presidential election falls in every year divisible by four
 * (the first in 1788), a midterm in every other even year. Null year (the
 * API hasn't said which cycle): a title true of any cycle. */
export function electionTitle(year: number | null | undefined, view: ElectionTitleView): string {
  if (year == null) {
    return {
      election_day: "Election day",
      results: "Election results",
      ballot: "General election ballot",
      elections: "General elections",
    }[view];
  }
  if (year % 4 === 2) {
    return {
      election_day: `${year} midterms: election day`,
      results: `${year} midterm results`,
      ballot: `${year} midterm ballot`,
      elections: `${year} midterm elections`,
    }[view];
  }
  const name = year % 4 === 0 ? "presidential election" : "general election";
  return {
    election_day: `${year} ${name}: election day`,
    results: `${year} ${name} results`,
    ballot: `${year} ${name} ballot`,
    elections: `${year} elections`,
  }[view];
}

/** The state ballot page's headline, worded by what its candidate lists are
 * (the API's ballotBasis, the weakest among its races). Only a list a state
 * itself has confirmed is "the ballot": FEC filers are registrations, and a
 * primary ballot is not November's. */
export function ballotHeadline(stateName: string, basis: BallotBasis["basis"] | undefined): string {
  if (basis === "filers") return `Everyone registered with the FEC to run in ${stateName}`;
  if (basis === "primary")
    return `Everyone running in ${stateName}, from the state's primary lists`;
  return `Everyone on ${stateName}'s ballot, and who is behind them`;
}

/** "federal candidates" or, while the lists are FEC filings, "registered
 * with the FEC": the header's count says what it counts. */
export function candidateCountLabel(
  count: number,
  basis: BallotBasis["basis"] | undefined
): string {
  if (basis === "filers") return `${count} registered with the FEC`;
  return `${count} federal ${count === 1 ? "candidate" : "candidates"}`;
}

/** "2020 and 2024" from a lean's election window as the API states it
 * ("2020+2024"), so no page names a window the data doesn't. Null when the
 * API gave none. */
export function leanWindowWords(window: string | null | undefined): string | null {
  const years = (window ?? "")
    .split("+")
    .map((y) => y.trim())
    .filter(Boolean);
  return years.length ? new Intl.ListFormat("en-US").format(years) : null;
}

/** Formats a signed PVI int as "R+3"/"D+3"/"EVEN" — display-only, not a computation. */
export function formatPvi(pvi: number | null): string {
  if (pvi == null) return "N/A";
  if (pvi === 0) return "EVEN";
  return pvi > 0 ? `R+${pvi}` : `D+${Math.abs(pvi)}`;
}

/** Formats Civitas's computed lean (one decimal) as "R+5.3"/"D+0.4"/"EVEN" —
 * display-only, the value is the backend's. */
export function formatComputedLean(lean: number | null | undefined): string {
  if (lean == null) return "N/A";
  const tenths = Math.round(Math.abs(lean) * 10) / 10;
  if (tenths === 0) return "EVEN";
  return `${lean > 0 ? "R" : "D"}+${tenths.toFixed(1)}`;
}

/** The page every computed-lean figure links to for its method. */
export const COMPUTED_LEAN_HREF = "/about/data#computed-lean";

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
  if (race.office === "S") return "Senate";
  if (race.district == null) return "House";
  return `House-${districtToken(race.district)}`;
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
 * `dropCountySuffix` exists. A state legislative row lists places and
 * then the counties they lie in: there, stripping the suffix turns
 * "Forsyth County" into "Forsyth", which is a different real Georgia
 * place (Forsyth city) sitting in the very same list.
 *
 * "(part)" is left as-is since it means that county is split across
 * districts. Null in, null out — a district missing from the crosswalk
 * stays unlabeled, never a guessed list.
 *
 * With a `query` (what the reader typed in the filter), the places that
 * matched it lead the label, whole and with their suffix: otherwise a
 * row can match on a place hidden behind "& 10 more", and a reader who
 * typed "Houston" (the city) sees one district come back with no sign it
 * matched Houston County, a different place. "Houston County, Anderson,
 * Cherokee & 9 more" says which Houston it found. */
/* parseUtc moved to lib/formatting.ts — it is a generic ISO-8601 concern,
   and the records band and homepage index need it too. Re-exported here so
   existing election call sites keep their import path. */
export { parseUtc } from "./formatting";

export function districtAreaLabel(
  areas: string[] | null,
  max = 3,
  dropCountySuffix = true,
  query = ""
): string | null {
  if (!areas || areas.length === 0) return null;
  const q = searchFold(query);
  const hit = (a: string) => q !== "" && searchFold(a).includes(q);
  const matched = areas.filter(hit);
  const rest = areas.filter((a) => !hit(a));
  const short = [
    ...matched,
    ...(dropCountySuffix ? rest.map((a) => a.replace(/ County\b/, "")) : rest),
  ];
  const shown = Math.max(max, matched.length);
  if (short.length <= shown) return short.join(", ");
  return `${short.slice(0, shown).join(", ")} & ${short.length - shown} more`;
}

type DistrictRow = {
  /** A U.S. House district is a number (0 = at-large); a state
   * legislative one is a string, because "10A" and "10B" are real. */
  district: number | string | null;
  areas: string[] | null;
  candidates: { name: string; ballotName?: string | null; displayName?: string | null }[];
};

/** Is `area` (a county, city, town or community) the place the reader
 * named in full? Its name as people say it ("Detroit" for "Detroit
 * city"), or a county's without "County" and "(part)". Never part of a
 * name: "Detroit" is not Detroit Beach. */
function namesArea(area: string, q: string): boolean {
  const bare = area.replace(/ \(part\)$/, "");
  return [bare, placeName(bare), bare.replace(/ County$/, "")].some((n) => searchFold(n) === q);
}

/** The district rows a filter box keeps for what the reader typed.
 *
 * When the query is some row's place or county named in full, only the
 * rows holding that place are kept (plus any matched by number or
 * candidate), so the list agrees with placeDistrictSummary's status line:
 * "Detroit" is in districts 12 and 13, and Detroit Beach (district 5)
 * no longer joins them by substring. Otherwise every row
 * matchesDistrictQuery accepts, so a part of a name still narrows. Every
 * district filter goes through this, never matchesDistrictQuery alone. */
export function filterDistricts<T>(rows: T[], query: string, toRow: (row: T) => DistrictRow): T[] {
  const q = searchFold(query);
  const whole = q !== "" && rows.some((r) => (toRow(r).areas ?? []).some((a) => namesArea(a, q)));
  return rows.filter((r) => matchesDistrictQuery(toRow(r), query, whole));
}

/** Does this district row match what the reader typed in the district
 * filter?
 *
 * Deliberately matches on the three things a reader plausibly knows
 * about themselves without being asked for an address: the city, town or
 * county they live in, a candidate's name (their representative's, when that member
 * is running again — a retiring member is on no row, and in a state that
 * redrew for this cycle the member's name finds the candidate's NEW
 * district, not necessarily the reader's), and the district number if
 * they happen to know it. Civitas never asks for
 * a street address, so the filter has to work from what a person can
 * recall unprompted — see the House section's own copy.
 *
 * `areas` is whatever place names that district is described by — the
 * counties and then the places (cities, towns, CDPs) on a U.S. House row,
 * the towns on a state legislative one.
 * Matching runs against that list IN FULL, not the truncated
 * districtAreaLabel display string: a reader typing "washington"
 * must still match a district whose label elided it behind "& 2 more".
 * Substring, compared through searchFold on both sides.
 */
export function matchesDistrictQuery(
  race: DistrictRow,
  query: string,
  /** Match a place only by its whole name (filterDistricts decides). */
  wholeName = false
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
    (q !== "" &&
      (race.areas ?? []).some((a) => (wholeName ? namesArea(a, q) : searchFold(a).includes(q)))) ||
    race.candidates.some(
      (c) =>
        q !== "" &&
        [c.name, c.ballotName, c.displayName].some((n) => searchFold(n ?? "").includes(q))
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

/** A Census place name as people say it: the legal type the Gazetteer
 * appends ("Tulsa city", "Brooklyn borough", "Juneau city and
 * borough", "… metropolitan government (balance)") dropped. The type is
 * always lower case and the name never starts a word with one, so
 * "Carson City" and "Lake Havasu City city" keep their own "City". */
export function placeName(place: string): string {
  return place.replace(/(?: (?:[a-z][a-z-]*|\(balance\)))+$/, "");
}

/** "11, 17 and 31". */
function andList(items: string[]): string {
  return items.length < 2
    ? items.join("")
    : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** What the House district filter says when the reader typed a city, town
 * or community by name: "Rivertown is split between districts 3, 7 and 11." A
 * place listed under several districts is split between them, and the
 * reader is told so rather than left to take the first row as theirs.
 * Matches the whole name only (accents, case and the legal type aside),
 * so "san" names nothing and the list alone answers. Two places with the
 * same name ("Kingston city", "Kingston town") keep their legal type. */
export function placeDistrictSummary(
  races: { district: number | string | null; places?: string[] | null }[],
  query: string
): string[] {
  return placesNamed(races, query).map(({ name, districts }) => {
    const nums = districts.map((d) => (d === 0 ? "at-large" : String(d)));
    if (districts.length === 1)
      return districts[0] === 0
        ? `${name} is in the at-large district.`
        : `${name} is in district ${nums[0]}.`;
    return `${name} is split between districts ${andList(nums)}.`;
  });
}

/** A place the reader named in full and the districts it lies in. */
export interface NamedPlace {
  /** The name as the page says it ("Springfield", or "Kingston town" when
   * two places share the short name). */
  name: string;
  /** The crosswalk's own name ("Springfield city"), which the district
   * shape files use too. */
  fullName: string;
  /** In order; a number for a House district, a key for a legislative one. */
  districts: (number | string)[];
}

/** The places (at most three) whose whole name is `query`, each with the
 * districts it lies in: more than one means the place is split. */
export function placesNamed(
  races: { district: number | string | null; places?: string[] | null }[],
  query: string
): NamedPlace[] {
  const q = searchFold(query);
  if (!q) return [];
  const found = new Map<string, (number | string)[]>();
  for (const r of races) {
    for (const p of r.places ?? []) {
      if (searchFold(placeName(p)) !== q && searchFold(p) !== q) continue;
      found.set(p, [...(found.get(p) ?? []), r.district ?? 0]);
    }
  }
  const shortNames = [...found.keys()].map(placeName);
  return [...found.entries()].slice(0, 3).map(([p, ds], i) => ({
    name: shortNames.filter((n) => n === shortNames[i]).length > 1 ? p : shortNames[i],
    fullName: p,
    districts: ds.sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true })),
  }));
}

/** What a candidate's money column says: the FEC's contributions for this
 * election, or in words why there is no figure. Never a fabricated $0 —
 * someone the state lists who never filed, someone not synced yet, and
 * someone with no FEC report for this election each say so. Read from the
 * figure, not `hasRaisedFunds`: the FEC's roster flag can say no money
 * while its totals report some (12 such candidates on 2026-10-08). */
export function raisedLabel(
  c: Pick<CandidateSummary, "fecFiled" | "lastFinancialsSync" | "contributions">
): string {
  if (c.fecFiled === false) return "no FEC filing";
  if (c.lastFinancialsSync == null) return "awaiting FEC sync";
  if (!c.contributions) return "no funds reported";
  return formatCurrency(c.contributions);
}

/** A candidate's name as the state prints it on its ballot, else the
 * FEC's. The one place a page picks between them. */
export function candidateName(c: {
  name: string;
  ballotName?: string | null;
  displayName?: string | null;
}): string {
  // displayName is the backend's choice (app/name_casing.py): the printed
  // ballot name verbatim, else the FEC name as written ("Jane Doe",
  // not "DOE, JANE"). An older backend or archive sends neither.
  return c.displayName || c.ballotName || c.name;
}

/** "Texas's 12th district, U.S. House", "Alaska at large, U.S. House",
 *  "Georgia, U.S. Senate": a race's seat, for its share card. */
export function raceSeat(
  race: { office: string; district: number | null },
  stateName: string
): string {
  if (race.office === "S") return `${stateName}, U.S. Senate`;
  if (!race.district) return `${stateName} at large, U.S. House`;
  const n = race.district;
  const suffix = n % 100 >= 11 && n % 100 <= 13 ? "th" : (["th", "st", "nd", "rd"][n % 10] ?? "th");
  return `${stateName}'s ${n}${suffix} district, U.S. House`;
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
      return "Removed from ballot";
    case "withdrawn":
      return "Withdrawn";
    case "under_appeal":
      return "Under appeal";
    default:
      return "On the ballot";
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

/** "House member, GA-6" / "Senator": what a candidate's memberRecord
 * says they are now, beside a record that isn't this seat's. */
export function memberRecordLabel(r: { chamber: "house" | "senate"; seat: string | null }): string {
  if (r.chamber === "senate") return "Senator";
  return r.seat ? `House member, ${r.seat}` : "House member";
}

/** FEC's incumbency codes, in words. */
const FEC_INCUMBENCY: Record<string, string> = {
  I: "Incumbent",
  C: "Challenger",
  O: "Open seat",
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
  return resultsMode ? "Member before this election" : "Sitting member";
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

/** A candidate's FEC page opened on one election's figures — the same
 * totals the ballot page shows (backend race_context.fec_candidate_url). */
export function fecCandidateUrl(candidateId: string, cycleYear: number): string {
  return `https://www.fec.gov/data/candidate/${encodeURIComponent(candidateId)}/?cycle=${cycleYear}&election_full=true`;
}

/** What a state-contest list drawn from primary results can miss: said
 * wherever such a list is shown (the state ballot page, the archive). */
export const PRIMARY_RESULTS_CAVEAT =
  "Names come from primary results, which can omit a nominee who ran unopposed, independent candidates (who run in no primary) and anyone a party named or replaced after the primary, so an office may be missing, or missing a party's nominee.";
