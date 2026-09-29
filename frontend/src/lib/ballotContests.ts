import type {
  RaceCoverageItem,
  RaceWithCandidates,
  StateBallot,
  StatewideRace,
} from "@/types/election";
import { candidateName, isActiveCandidate, majorPartyOf } from "@/lib/elections";

/** One contest as the ballot page lays it out: a box in one of three
 * printed-ballot columns on desktop, a row in the index on a phone, and
 * one screen of detail in the drawer. The order of the list IS the ballot's
 * order — federal offices, then the state's own, then measures and local —
 * and Back/Next walk it.
 *
 * Only presentation is decided here. Which candidates a race lists, and
 * what kind of list it is, stay the API's call (candidateSource,
 * ballotBasis); a section the API says nobody has checked is simply not a
 * contest, because the page's "not on this page" list already names it. */
export type ContestKind =
  | "senate"
  | "house"
  | "statewide"
  | "stateleg"
  | "judicial"
  | "stateNone"
  | "measures"
  | "local"
  | "news";

export type BallotColumn = "federal" | "state" | "local";

export interface BallotContest {
  /** Stable, URL-safe: the drawer's hash is `#ballot-${key}`. */
  key: string;
  kind: ContestKind;
  column: BallotColumn;
  /** The office as a ballot prints it. Federal offices are always
   * "U.S. Senator" / "U.S. Representative" — one naming, everywhere. */
  title: string;
  subtitle: string;
  /** The ballot's own instruction line, where one applies. */
  instruction: string | null;
  /** The phone index's second line. */
  summary: string;
  /** Senate contests carry their race. */
  race?: RaceWithCandidates;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function storiesFor(coverage: RaceCoverageItem[], raceId: string): number {
  return coverage.filter((c) => c.race?.id === raceId).length;
}

// Federal terms are fixed by the Constitution (art. I, §2 and §3); a
// special Senate election fills only what is left of the vacated term.
const HOUSE_TERM = "2-year term";
const SENATE_TERM = "6-year term";
const SPECIAL_TERM = "fills the rest of the term";

/** "Open seat" / "Incumbent running" — read from the FEC's own incumbency
 * code on the race's candidates, never inferred from names. */
function seatLine(race: RaceWithCandidates): string {
  const incumbent = race.candidates.find((c) => c.incumbentChallenge === "I");
  const seat = incumbent ? `${candidateName(incumbent)} running again` : "Open seat";
  return race.isSpecial
    ? `Special election · ${seat} · ${SPECIAL_TERM}`
    : `${seat} · ${SENATE_TERM}`;
}

export type StatewideGroup = { key: string; race?: StatewideRace; seats?: StatewideRace[] };

/** A contest listing two nominees of one major party fills more than one
 * seat. Major parties only: two nonpartisan ("N") or two minor-party
 * ("OTH") nominees can be rivals for a single seat. */
function fillsSeveralSeats(race: StatewideRace): boolean {
  const majors = (race.nominees ?? []).map(majorPartyOf).filter((party) => party !== null);
  return new Set(majors).size < majors.length;
}

/** Statewide rows in the backend's order, with every seat of one body (the
 * rows sharing an officeCode that carry a seat) gathered under that body:
 * New Hampshire's five Executive Council districts are one office, not five. */
export function groupStatewideRaces(races: StatewideRace[]): StatewideGroup[] {
  const groups: StatewideGroup[] = [];
  for (const race of races) {
    const last = groups[groups.length - 1];
    if (race.seat && race.officeCode) {
      if (last?.seats && last.seats[0].officeCode === race.officeCode) {
        last.seats.push(race);
      } else {
        groups.push({ key: race.officeCode, seats: [race] });
      }
    } else {
      groups.push({ key: race.office, race });
    }
  }
  return groups;
}

/** How many statewide contests one voter marks: each office, and for a
 * body with seats, one seat where each voter votes in a single district's
 * (New Hampshire's Executive Council) or every seat where each voter votes
 * for all of them (Georgia's PSC). A body whose election the backend does
 * not cite counts each seat, as every row did before seats were grouped. */
export function countStatewideContests(races: StatewideRace[]): number {
  return groupStatewideRaces(races).reduce(
    (n, g) => n + (g.seats ? (g.seats[0].electedBy === "district" ? 1 : g.seats.length) : 1),
    0
  );
}

export function buildBallotContests(ballot: StateBallot, hasTowns: boolean): BallotContest[] {
  const contests: BallotContest[] = [];

  for (const race of ballot.senateRaces) {
    const stories = storiesFor(ballot.coverage, race.id);
    contests.push({
      key: `senate-${race.id}`,
      kind: "senate",
      column: "federal",
      title:
        ballot.senateRaces.length > 1 && race.isSpecial
          ? "U.S. Senator (special election)"
          : "U.S. Senator",
      subtitle: seatLine(race),
      instruction: "Vote for one",
      summary: `${plural(race.candidates.filter(isActiveCandidate).length, "candidate")} · ${plural(stories, "story", "stories")}`,
      race,
    });
  }

  if (ballot.houseRaces.length > 0) {
    const n = ballot.houseRaces.length;
    contests.push({
      key: "house",
      kind: "house",
      column: "federal",
      title: "U.S. Representative",
      subtitle:
        n === 1
          ? `One statewide seat · ${HOUSE_TERM}`
          : `${n} districts · you vote in one · ${HOUSE_TERM}`,
      instruction: "Vote for one",
      summary:
        n === 1
          ? plural(ballot.houseRaces[0].candidates.filter(isActiveCandidate).length, "candidate")
          : `${n} districts · pick yours`,
    });
  }

  // Optional-chained: an API response is a trust boundary, and a partial
  // one must cost this contest, not the whole page.
  if (ballot.statewideCoverage?.status && ballot.statewideCoverage.status !== "not_yet_covered") {
    // Offices, not rows: a body's seats are one office (groupStatewideRaces).
    const n = groupStatewideRaces(ballot.statewideRaces ?? []).length;
    const offices = plural(n, "office");
    // Primary results itemise only contested nominations; the box (and any
    // image shared of it) says which kind of list it is, as the drawer does.
    const basis = ballot.statewideCoverage.ballotList ? "" : " · from primary results";
    contests.push({
      key: "statewide",
      kind: "statewide",
      column: "state",
      title: "Statewide offices",
      subtitle: n === 0 ? "None on this ballot" : `${offices}${basis}`,
      // Not where one contest fills several seats (North Dakota's PSC lists
      // two nominees per party, and a voter marks two): the API carries no
      // seat count per contest, so the box can't say how many to mark.
      instruction:
        n === 0 || (ballot.statewideRaces ?? []).some(fillsSeveralSeats)
          ? null
          : "Vote for one in each",
      summary: n === 0 ? "None on this ballot" : offices,
    });
  }

  if (ballot.stateLegRaces.length > 0) {
    const seats = ballot.stateLegRaces.reduce((sum, c) => sum + c.districts.length, 0);
    // The legislature is read from the same list as the statewide offices.
    const basis = ballot.statewideCoverage?.ballotList ? "" : " · from primary results";
    contests.push({
      key: "stateleg",
      kind: "stateleg",
      column: "state",
      title: ballot.stateLegRaces.map((c) => c.label).join(" · "),
      subtitle: `Your seats depend on where you live${basis}`,
      instruction: "One seat per chamber",
      summary: `${plural(seats, "seat")} contested · find yours`,
    });
  }

  if (ballot.judicialCoverage && ballot.judicialCoverage.status !== "not_yet_covered") {
    const courts = ballot.judicialRaces.length;
    const basis = ballot.judicialCoverage.ballotList ? "" : " · from primary results";
    contests.push({
      key: "judicial",
      kind: "judicial",
      column: "state",
      title: "Judges",
      subtitle: courts === 0 ? "None on this ballot" : `${plural(courts, "court")}${basis}`,
      instruction: null,
      summary: courts === 0 ? "None on this ballot" : plural(courts, "court"),
    });
  }

  // No state office of any kind on file: say so in the column rather than
  // leave it empty, the same "not loaded is not none" rule the measures
  // box follows. An empty column reads as a state that elects nobody.
  if (!contests.some((c) => c.column === "state")) {
    contests.push({
      key: "state-offices",
      kind: "stateNone",
      column: "state",
      title: "State offices",
      subtitle: "Not loaded yet — check the official lookup",
      instruction: null,
      summary: "Not loaded yet",
    });
  }

  // Measures still on the ballot: one removed or withdrawn is shown (marked)
  // for a grace window, but is not something a voter marks.
  const measures = ballot.measures.filter(
    (m) => m.status !== "removed" && m.status !== "withdrawn"
  ).length;
  const listed = ballot.measures.length;
  const none = ballot.measureCoverage.status === "confirmed_none";
  // "None on this ballot" is the state's own answer. After measures were
  // dropped, or when "none" is our operator's call (basis "operator"), it
  // is "none remaining"; a dropped list that no read has confirmed as
  // empty is not "none" at all.
  const noneLine =
    none && (listed > 0 || ballot.measureCoverage.basis === "operator")
      ? "None remaining"
      : none
        ? "None on this ballot"
        : listed > 0
          ? "None current"
          : "Not loaded yet";
  contests.push({
    key: "measures",
    kind: "measures",
    column: "local",
    title: "Statewide ballot measures",
    subtitle:
      measures > 0
        ? plural(measures, "measure")
        : none
          ? noneLine
          : `${noneLine} — check the official lookup`,
    instruction: measures > 0 ? "Yes or no on each" : null,
    summary: measures > 0 ? plural(measures, "measure") : noneLine,
  });

  if (hasTowns) {
    contests.push({
      key: "local",
      kind: "local",
      column: "local",
      title: "Local contests",
      subtitle: "Council, school board, local questions",
      instruction: null,
      summary: "Choose your town",
    });
  }

  contests.push({
    key: "news",
    kind: "news",
    column: "local",
    title: "News coverage",
    subtitle: "Every race on this ballot",
    instruction: null,
    summary: plural(ballot.coverage.length, "story", "stories"),
  });

  return contests;
}

/** The link fragment that opens a contest (read back by `contestForHash`):
 * the race's own `#race-{id}` where the contest is one race (or a House
 * district was picked), else `#ballot-{key}`. */
export function contestHash(contest: BallotContest, houseRaceId: string | null = null): string {
  if (houseRaceId) return `#race-${houseRaceId}`;
  return contest.race ? `#race-${contest.race.id}` : `#ballot-${contest.key}`;
}

/** Which contest a `#race-{id}` link (old /elections/{raceId} redirects,
 * Bluesky posts) or a `#ballot-{key}` link points at, and for a House
 * link, which district. */
export function contestForHash(
  hash: string,
  contests: BallotContest[],
  ballot: StateBallot
): { key: string; houseRaceId: string | null } | null {
  const race = hash.match(/^#race-(.+)$/)?.[1];
  if (race) {
    if (ballot.houseRaces.some((r) => r.id === race)) return { key: "house", houseRaceId: race };
    const senate = contests.find((c) => c.race?.id === race);
    return senate ? { key: senate.key, houseRaceId: null } : null;
  }
  const key = hash.match(/^#ballot-(.+)$/)?.[1];
  return key && contests.some((c) => c.key === key) ? { key, houseRaceId: null } : null;
}

/**
 * How many contests a voter in this state marks on the ballot: each Senate
 * race, one House race (a voter is in one district), each statewide office
 * (one seat of a body elected by district; see countStatewideContests),
 * one seat per state legislative chamber, each court, and each measure.
 * The page's sections are not contests — "News coverage", the local-contests
 * pointer and the "not loaded yet" placeholders used to be counted as ones,
 * so a state with two federal races and nothing else loaded read "5 contests".
 */
export function countBallotContests(contests: BallotContest[], ballot: StateBallot): number {
  let n = 0;
  for (const c of contests) {
    if (c.kind === "senate" || c.kind === "house") n += 1;
    else if (c.kind === "statewide") n += countStatewideContests(ballot.statewideRaces);
    else if (c.kind === "stateleg") n += ballot.stateLegRaces.length;
    else if (c.kind === "judicial") n += ballot.judicialRaces.length;
    else if (c.kind === "measures")
      n += ballot.measures.filter((m) => m.status !== "removed" && m.status !== "withdrawn").length;
  }
  return n;
}
