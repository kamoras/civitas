/**
 * Live election results: colour, labels and wording for the results pages.
 *
 * Every sentence here is a fixed frame around the state's own numbers,
 * matching the backend's templates (live_results/bluesky.py):
 * a race is "leading" until the state itself calls its count official, and
 * nothing on the page projects or calls a winner.
 */

import type {
  ElectionPhaseInfo,
  LiveRaceResult,
  LiveResults,
  ResultEvent,
  ResultEventPerson,
} from "@/types/election";

const DEM = "130,172,255"; // dem-blue #82acff
const REP = "255,137,137"; // rep-red #ff8989
const OTHER = "201,149,255"; // ind-purple #c995ff

/** Fill for a state or district with no count yet, in a state read live. */
export const AWAITING_FILL = "#2a2520";
/** Fill for a place this page has no live count for at all. */
export const UNCOVERED_FILL = "rgba(255, 255, 255, 0.05)";
/** Fill for a state read live whose latest feed read failed and which has
 * no count to show: not "no votes yet" — a feed that's down says nothing
 * about whether counting has begun. Amber, the colour of every
 * couldn't-read notice on these pages. */
export const FEED_FAILED_FILL = "rgba(255, 216, 77, 0.28)";
/** Votes counted, nobody ahead: an exact tie, or a House delegation split
 * evenly. Not "no votes yet", which is AWAITING_FILL. */
export const TIED_FILL = "rgba(205, 199, 188, 0.45)";
/** Fill for a state read live whose last polls are still open: nothing of
 * its count is read or said until they close, so not "no votes yet" — a
 * statement about the count — but voting under way. Faint cyan. */
export const POLLS_OPEN_FILL = "rgba(77, 227, 232, 0.16)";

/** A House district in a state whose feed has answered but which has no
 * count of its own from it — a contest the feed doesn't list or that
 * couldn't be matched to the race, an uncontested seat. Neither "no votes
 * yet" (the state is counting) nor uncovered (the state is read live):
 * drawn hatched. The stripe colour; NO_COUNT_SWATCH is the legend key. */
export const NO_COUNT_STRIPE = "rgba(205, 199, 188, 0.4)";
export const NO_COUNT_SWATCH = `repeating-linear-gradient(45deg, ${NO_COUNT_STRIPE} 0 2px, transparent 2px 5px)`;

/** Whether the page should lead with results rather than research: only
 * the two results phases the backend names. A missing or malformed phase
 * (an older backend mid-rollout, a cached error body) is the campaign
 * page — never results by default. */
export function showsResults(phase: ElectionPhaseInfo | null | undefined): boolean {
  return phase?.phase === "election_day" || phase?.phase === "results";
}

type PollsInfo = Pick<LiveResults, "phase" | "pollsClose" | "feeds" | "races">;

/** Whether `state`'s last polls are known to have closed. The `results`
 * phase is the day after election day, when every state's have; a count
 * already stored for the state means they have (nothing is read before);
 * otherwise the backend's closing time decides, or — from an older backend
 * that sends none — a feed read that got past the polls-open gate. Unknown
 * is "not closed": the page never talks about a count while people may
 * still be voting. */
export function pollsClosed(results: PollsInfo, state: string, now: number): boolean {
  if (results.phase?.phase === "results") return true;
  if (results.races.some((r) => r.state === state)) return true;
  const close = Date.parse(results.pollsClose?.[state] ?? "");
  if (!Number.isNaN(close)) return now >= close;
  const feed = results.feeds?.[state];
  return !!feed && feed.status !== "polls_open";
}

/** Whether a state read live is known to be still voting: its closing time
 * is ahead (or, from an older backend with none, its feed says polls open)
 * and no count is stored. The closing time wins over a feed status read
 * minutes before it passed. */
export function pollsStillOpen(results: PollsInfo, state: string, now: number): boolean {
  if (results.phase?.phase === "results") return false;
  if (results.races.some((r) => r.state === state)) return false;
  const close = Date.parse(results.pollsClose?.[state] ?? "");
  if (!Number.isNaN(close)) return now < close;
  return results.feeds?.[state]?.status === "polls_open";
}

/** Whether a state's last feed read failed to give a count this page could
 * use — down, refused as test or mismatched data, or older than the one
 * already shown. */
export function feedFailed(feed: { status: string } | null | undefined): boolean {
  return !!feed && ["untrusted", "unavailable", "stale", "failed"].includes(feed.status);
}

/** Votes counted and the top two level: an exact tie, where the backend
 * leaves leaderParty null. Read from the tallies (sorted, most votes first)
 * rather than from a null leaderParty, which a leader whose party the feed
 * doesn't name also has. Not a lead for whoever the feed lists first. */
export function isTied(r: { votesCounted: number; candidates: { votes: number }[] }): boolean {
  const [first, second] = r.candidates;
  return r.votesCounted > 0 && !!first && !!second && first.votes === second.votes;
}

/** Share of reporting units in, 0..1, or null when the state gives none. */
export function reportingShare(r: {
  reportingUnits: number | null;
  totalUnits: number | null;
}): number | null {
  if (!r.totalUnits || r.reportingUnits == null) return null;
  return Math.min(1, r.reportingUnits / r.totalUnits);
}

function rgb(party: string | null): string {
  if (party === "DEM") return DEM;
  if (party === "REP") return REP;
  return OTHER;
}

/** A race's map fill: its leader's party (purple for one the feed gives no
 * party the vocabulary knows), fainter (lower opacity, so dimmer over the
 * dark map — never lighter) while fewer than half the
 * units are in, solid once the state calls it official. */
export function resultFill(result: LiveRaceResult | undefined, covered: boolean): string {
  if (!result) return covered ? AWAITING_FILL : UNCOVERED_FILL;
  if (!(result.votesCounted > 0)) return AWAITING_FILL;
  if (isTied(result)) return TIED_FILL;
  if (result.official) return `rgba(${rgb(result.leaderParty)}, 1)`;
  const share = reportingShare(result);
  const opacity = share == null ? 0.55 : share < 0.5 ? 0.3 : 0.45 + 0.45 * share;
  return `rgba(${rgb(result.leaderParty)}, ${opacity.toFixed(2)})`;
}

/** Tailwind text colour for a party. */
export function partyTextClass(party: string | null | undefined): string {
  if (party === "DEM") return "text-dem-blue";
  if (party === "REP") return "text-rep-red";
  if (party === "IND") return "text-ind-purple";
  return "text-ink-lo";
}

export function partyBarColor(party: string | null | undefined): string {
  if (party === "DEM") return "#82acff";
  if (party === "REP") return "#ff8989";
  if (party === "IND") return "#c995ff";
  return "#8f8980";
}

const LETTER: Record<string, string> = {
  DEM: "D",
  REP: "R",
  IND: "I",
  LIB: "L",
  GRE: "G",
  CON: "C",
};
const HOLDERS: Record<string, string> = {
  DEM: "Democrats",
  REP: "Republicans",
  IND: "independents",
  LIB: "Libertarians",
  GRE: "Greens",
};
const HOLDER: Record<string, string> = {
  DEM: "a Democrat",
  REP: "a Republican",
  IND: "an independent",
  LIB: "a Libertarian",
  GRE: "a Green",
  CON: "a Constitution Party member",
};

/** "a Democrat" — who held the seat going in, for "held by …". */
export function heldByPhrase(party: string | null | undefined): string {
  return (party && HOLDER[party]) || "another party";
}

export function partyLetter(party: string | null | undefined): string {
  return party ? (LETTER[party] ?? party) : "";
}

export function withParty(p: { name: string; party: string | null } | null | undefined): string {
  if (!p) return "";
  const letter = partyLetter(p.party);
  return letter ? `${p.name} (${letter})` : p.name;
}

function share(p: ResultEventPerson | null | undefined): string {
  if (!p) return "";
  return p.pct != null ? `${withParty(p)} ${p.pct}%` : withParty(p);
}

/** "GA Senate", "GA Senate (special)", "GA-2", "AK at-large". */
export function raceLabel(r: {
  state: string;
  office: string;
  district: number | null;
  isSpecial?: boolean;
}): string {
  if (r.office === "S") return `${r.state} Senate${r.isSpecial ? " (special)" : ""}`;
  return r.district ? `${r.state}-${r.district}` : `${r.state} at-large`;
}

/** "40 of 64 counties reporting (63%)", or "" when the state gives no count. */
export function reportingText(r: {
  reportingUnits?: number | null;
  totalUnits?: number | null;
  unitLabel?: string;
}): string {
  if (!r.totalUnits || r.reportingUnits == null) return "";
  const pct = Math.round((100 * r.reportingUnits) / r.totalUnits);
  return `${r.reportingUnits.toLocaleString("en-US")} of ${r.totalUnits.toLocaleString("en-US")} ${
    r.unitLabel ?? "precincts"
  } reporting (${pct}%)`;
}

function sentence(...parts: string[]): string {
  return parts
    .filter(Boolean)
    .map((p) => (/[.!?]$/.test(p) ? p : `${p}.`))
    .join(" ");
}

export interface UpdateText {
  /** Short tag for the feed: FLIP, LEAD, OFFICIAL, ALL IN, FIRST, UPDATE. */
  tag: string;
  tone: "flip" | "lead" | "official" | "neutral";
  text: string;
}

/** One live update, worded from the event's own figures. */
export function describeUpdate(event: ResultEvent): UpdateText {
  const d = event.detail ?? {};
  // No leader with votes counted is an exact tie: the backend then sends the
  // second of the two level candidates as runnerUp, and naming only them
  // ("Sam Roe (R) 50%") reads as their lead. Say tied and name neither.
  const tied = !d.leader && ((d.votesCounted ?? 0) > 0 || (d.runnerUp?.votes ?? 0) > 0);
  const shares = tied
    ? d.runnerUp?.pct != null
      ? `The top two are tied at ${d.runnerUp.pct}% each`
      : "The top two are tied"
    : d.leader
      ? [share(d.leader), share(d.runnerUp)].filter(Boolean).join(", ")
      : "";
  const reporting = reportingText(d);
  const holders = d.heldBy ? (HOLDERS[d.heldBy] ?? d.heldBy) : "another party";
  switch (event.kind) {
    case "first_returns":
      return { tag: "FIRST", tone: "neutral", text: sentence("First returns", reporting, shares) };
    case "lead_change":
      return {
        tag: "LEAD",
        tone: "lead",
        text: sentence(
          d.previousLeader
            ? `${withParty(d.leader)} moves ahead of ${withParty(d.previousLeader)}`
            : `${withParty(d.leader)} moves ahead`,
          shares,
          reporting
        ),
      };
    case "all_reporting":
      return {
        tag: "ALL IN",
        tone: "neutral",
        text: sentence(
          `All ${(d.totalUnits ?? 0).toLocaleString("en-US")} ${d.unitLabel ?? "precincts"} have reported`,
          shares,
          "Counting can continue after every unit reports"
        ),
      };
    case "official":
      return {
        tag: "OFFICIAL",
        tone: "official",
        text: sentence("The state lists its count as official", shares),
      };
    case "flip":
      return {
        tag: "FLIP",
        tone: "flip",
        // "Leads" even in a count the state lists as official: that is not
        // a result (a Georgia general short of a majority goes to a
        // runoff), and nothing on the page calls a race.
        text: d.official
          ? sentence(
              `${withParty(d.leader)} leads in the count the state lists as official, in a seat ${holders} hold`,
              shares
            )
          : sentence(
              `${withParty(d.leader)} leads in a seat ${holders} hold`,
              shares,
              reporting,
              "Not final"
            ),
      };
    case "flip_reversed":
      // The holder's party ahead again — or nobody: an exact tie has no
      // leader to name.
      return {
        tag: "UPDATE",
        tone: "lead",
        text: sentence(
          d.leader && d.leader.party === d.heldBy
            ? `${withParty(d.leader)} is ahead again, so the seat no longer shows a change of party`
            : !d.leader && (d.votesCounted ?? 0) > 0
              ? "The count is now tied, so the seat no longer shows a change of party"
              : "The count no longer shows the seat changing party",
          reporting
        ),
      };
    default:
      return { tag: "UPDATE", tone: "neutral", text: sentence(shares, reporting) };
  }
}

/** "Nov 3, 9:42 PM ET" — election night is told in Eastern time, as every
 * network does, whatever the reader's own zone. Always with its date: the
 * results stay up for weeks, and a bare "9:42 PM" read on the 15th means
 * the wrong night. */
export function formatEasternTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })} ET`;
}

export interface StateResultSummary {
  senate: LiveRaceResult[];
  house: LiveRaceResult[];
  /** House seats led, by party. */
  houseLeads: Record<string, number>;
  flips: number;
}

export function summarizeState(races: LiveRaceResult[]): StateResultSummary {
  const senate = races.filter((r) => r.office === "S");
  const house = races.filter((r) => r.office === "H");
  return { senate, house, houseLeads: seatsLed(house), flips: races.filter((r) => r.flip).length };
}

/** "D 3 · R 2 · I 1" — seats led by party, the two majors always named and
 * any other party that leads one after them (as LedTally draws it). */
export function formatLed(led: Record<string, number>): string {
  const others = Object.entries(led)
    .filter(([p, n]) => p !== "DEM" && p !== "REP" && n > 0)
    .map(([p, n]) => `${partyLetter(p)} ${n}`);
  return [`D ${led.DEM ?? 0}`, `R ${led.REP ?? 0}`, ...others].join(" · ");
}

/** Seats led, by party, over a set of races. A leader the feed gives no
 * party the vocabulary knows still leads a seat: counted as "OTHER", not
 * dropped. A tie, or nothing counted, is nobody's. */
export function seatsLed(races: LiveRaceResult[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of races) {
    if (!(r.votesCounted > 0) || isTied(r)) continue;
    const party = r.leaderParty ?? "OTHER";
    out[party] = (out[party] ?? 0) + 1;
  }
  return out;
}

/** The fill for a state on the national map, by chamber: its Senate race's
 * leader, or — for House — the party leading more of its seats (fainter for
 * a split delegation). */
export function stateFill(
  races: LiveRaceResult[],
  chamber: "S" | "H",
  covered: boolean,
  hasRace: boolean,
  /** The state's latest feed read failed (feedFailed): with no count to
   * show, it is drawn as FEED_FAILED_FILL, never as "no votes yet". */
  feedDown = false,
  /** The state's polls are still open (pollsStillOpen): drawn as
   * POLLS_OPEN_FILL, which says nothing about the count. */
  pollsOpen = false
): string {
  if (!hasRace) return UNCOVERED_FILL;
  const mine = races.filter((r) => r.office === chamber);
  if (!mine.length && covered && pollsOpen) return POLLS_OPEN_FILL;
  if (!mine.length && covered && feedDown) return FEED_FAILED_FILL;
  if (chamber === "S") return resultFill(mine[0], covered);
  if (!mine.length) return covered ? AWAITING_FILL : UNCOVERED_FILL;
  const leads = seatsLed(mine);
  const d = leads.DEM ?? 0;
  const r = leads.REP ?? 0;
  const others = Object.entries(leads).reduce(
    (n, [p, c]) => (p === "DEM" || p === "REP" ? n : n + c),
    0
  );
  if (!d && !r) {
    if (others) return `rgba(${OTHER}, 0.6)`;
    return mine.some((x) => x.votesCounted > 0) ? TIED_FILL : AWAITING_FILL;
  }
  if (d === r) return TIED_FILL;
  const party = d > r ? "DEM" : "REP";
  const margin = Math.abs(d - r) / (d + r);
  return `rgba(${rgb(party)}, ${(0.35 + 0.55 * margin).toFixed(2)})`;
}
