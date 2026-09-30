/**
 * Live election results: colour, labels and wording for the results pages.
 *
 * Every sentence here is a fixed frame around the state's own numbers,
 * matching the backend's templates (live_results/bluesky.py):
 * a race "leads", even once the state lists its count as official, and
 * nothing on the page projects or calls a winner — Civitas calls no race.
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
 * drawn hatched, over AWAITING_FILL (DistrictMap's pattern). The stripe
 * colour; NO_COUNT_SWATCH is the legend key. At 0.7 the stripe, as drawn,
 * stands at least 3:1 against its own base and every other count-less fill
 * and the page (WCAG 1.4.11; results.test.ts) — at 0.4 it was 2.66:1
 * against its base. */
export const NO_COUNT_STRIPE = "rgba(205, 199, 188, 0.7)";
export const NO_COUNT_SWATCH = `repeating-linear-gradient(45deg, ${NO_COUNT_STRIPE} 0 2px, transparent 2px 5px)`;

/*
 * The fills that say "no count to colour" — polls open, no votes yet, feed
 * not read — are all dark and within about 1.2:1 of each other and of a
 * state with no live feed, so on the maps each also carries a texture whose
 * marks stand at least 3:1 against every one of those fills and the page
 * (WCAG 1.4.11): polls open is striped cyan one way, a feed not read
 * striped amber the other, no votes yet dotted. A state with no live feed
 * is the only plain dark one. The map's legend draws the same textures
 * (the *_SWATCH values), and every state's accessible name says its status
 * in words (stateShade), so neither colour nor texture is the only cue.
 */
export const POLLS_OPEN_MARK = "rgba(77, 227, 232, 0.8)";
export const AWAITING_MARK = "rgba(232, 228, 220, 0.6)";
export const FEED_FAILED_MARK = "rgba(255, 216, 77, 0.8)";
export const POLLS_OPEN_SWATCH = `repeating-linear-gradient(45deg, ${POLLS_OPEN_MARK} 0 1.5px, transparent 1.5px 5px)`;
export const FEED_FAILED_SWATCH = `repeating-linear-gradient(-45deg, ${FEED_FAILED_MARK} 0 1.5px, transparent 1.5px 5px)`;
export const AWAITING_SWATCH = `radial-gradient(circle, ${AWAITING_MARK} 0 0.9px, transparent 1.1px) 0 0 / 4px 4px`;

/*
 * A count that is no longer being refreshed — its state's latest feed read
 * failed, or the backend has stopped reading it (stateFeedBehind) — keeps
 * its leader's colour (it is still who led when last read) with the same
 * amber stripe as a feed not read laid over it, so it never passes for a
 * live count. Each amber stripe runs beside a dark one: over the lighter
 * party fills the dark stripe is the one that stands out, over the fainter
 * ones the amber (results.test.ts checks at least one is 3:1 against every
 * fill a count can have). The state's accessible name says "not live" too.
 */
export const STALE_SHADOW = "rgba(20, 17, 14, 0.9)";
export const STALE_SWATCH = `repeating-linear-gradient(-45deg, ${FEED_FAILED_MARK} 0 1.5px, ${STALE_SHADOW} 1.5px 3px, transparent 3px 6px)`;

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

/** How often the backend reads every covered state's feed
 * (scheduler._election_results_sync): every five minutes, and hourly once
 * no race's count has changed for a day. Every pass records each state's
 * read (LiveResultRead.checkedAt), polls-open states included. */
export const RESULTS_SYNC_MS = 5 * 60_000;
export const RESULTS_SYNC_SETTLED_MS = 60 * 60_000;
const SETTLED_AFTER_MS = 24 * 3_600_000;
/** How far past its due pass a state's last read may be before the page
 * stops calling its count live: a pass that is slow (a feed timing out) or
 * skipped once is not a stopped sync; two missed five-minute passes are. */
export const FEED_BEHIND_SLACK_MS = 10 * 60_000;

/** The wait between the backend's reads of the feeds, as its scheduler
 * decides it from the last change to any count. */
export function resultsSyncInterval(
  phase: Pick<ElectionPhaseInfo, "lastResultChange"> | null | undefined,
  now: number
): number {
  const last = Date.parse(phase?.lastResultChange ?? "");
  return !Number.isNaN(last) && now - last > SETTLED_AFTER_MS
    ? RESULTS_SYNC_SETTLED_MS
    : RESULTS_SYNC_MS;
}

/** Whether a state's feed hasn't been read for well over a sync pass —
 * more than 15 minutes while counts move, 70 once hourly: the backend's
 * sync has stopped or is stuck, so whatever count the page holds for the
 * state is not a live one, however its last read went. False with no
 * record to judge by (an older backend). */
export function feedBehind(
  feed: { checkedAt: string | null } | null | undefined,
  phase: Pick<ElectionPhaseInfo, "lastResultChange"> | null | undefined,
  now: number
): boolean {
  const checked = Date.parse(feed?.checkedAt ?? "");
  if (Number.isNaN(checked)) return false;
  return now - checked > resultsSyncInterval(phase, now) + FEED_BEHIND_SLACK_MS;
}

/** A state read live that has no read record at all, although the backend
 * keeps one (`feeds` is sent) and its polls closed well over a sync pass
 * ago, is behind too: every pass records every covered state's read, so no
 * record by then means the sync never reached it. Otherwise as
 * feedBehind. False from an older backend that keeps no records, and for a
 * state with no known closing time. */
export function stateFeedBehind(
  results: Pick<LiveResults, "phase" | "pollsClose" | "feeds">,
  state: string,
  now: number
): boolean {
  const feed = results.feeds?.[state];
  if (feed && !Number.isNaN(Date.parse(feed.checkedAt ?? "")))
    return feedBehind(feed, results.phase, now);
  if (!results.feeds) return false;
  const close = Date.parse(results.pollsClose?.[state] ?? "");
  if (Number.isNaN(close)) return false;
  return now - close > resultsSyncInterval(results.phase, now) + FEED_BEHIND_SLACK_MS;
}

/** How far the page lets its clock run on from the last answer it got,
 * while its refreshes are succeeding: one poll interval
 * (useLiveResults' RESULTS_POLL_MS). Enough for a state's polls to close
 * on time between two polls; not enough for a tab left hidden for an hour
 * (whose polling stops) to call every feed behind in the moment before its
 * catch-up request answers. */
const CLOCK_RUN_ON_MS = 60_000;

/**
 * The time the results pages judge the count by: the server's clock as of
 * the last answer (its Date header, LiveResults.clock), run on by the time
 * since it arrived — at most CLOCK_RUN_ON_MS, and not at all while the
 * page's own refreshes are failing (`refreshing` false). Never the
 * browser's clock alone: one a quarter of an hour fast would call every
 * feed behind, one three hours slow would keep saying polls are open long
 * after they closed. And while this page can't refresh, nothing it holds
 * gets older in its own eyes — whether a feed has fallen behind is judged
 * as the last answer stood; the refresh failure is the page's to say, not
 * the feeds'. Without a clock (an older caller) it is `browserNow`.
 */
export function resultsNow(
  results: Pick<LiveResults, "clock"> | null | undefined,
  browserNow: number,
  refreshing = true
): number {
  const clock = results?.clock;
  if (!clock) return browserNow;
  const elapsed = refreshing
    ? Math.min(Math.max(browserNow - clock.receivedAt, 0), CLOCK_RUN_ON_MS)
    : 0;
  return (clock.serverDate ?? clock.receivedAt) + elapsed;
}

/** The oldest and newest times the counts on screen were read, over every
 * state with a count (countReadAt per state). Null with no count read. A
 * page that can't refresh says both, so the newest state's read never
 * stands for all of them. */
export function countReadRange(
  results: Pick<LiveResults, "feeds" | "races">
): { oldest: string; newest: string } | null {
  const times = [...new Set(results.races.map((r) => r.state))]
    .map((st) => countReadAt(results, st))
    .filter((t): t is string => !!t && !Number.isNaN(Date.parse(t)))
    .sort((a, b) => Date.parse(a) - Date.parse(b));
  return times.length ? { oldest: times[0], newest: times[times.length - 1] } : null;
}

/** When the count on screen was read from the feeds: a state's last good
 * read, or — from an older backend with no feed record — the newest read
 * of any of its races. Without `state`, the newest of every state's. Never
 * the page's own clock: a refresh can be answered from a cache, so "now"
 * would overstate how fresh the count is. Null with no count read. */
export function countReadAt(
  results: Pick<LiveResults, "feeds" | "races">,
  state?: string
): string | null {
  const newest = (times: (string | null | undefined)[]) =>
    times.reduce<string | null>(
      (latest, t) =>
        t && !Number.isNaN(Date.parse(t)) && (!latest || Date.parse(t) > Date.parse(latest))
          ? t
          : latest,
      null
    );
  const races = state ? results.races.filter((r) => r.state === state) : results.races;
  if (state) return results.feeds?.[state]?.lastOkAt ?? newest(races.map((r) => r.fetchedAt));
  return newest([
    ...Object.values(results.feeds ?? {}).map((f) => f.lastOkAt),
    ...races.map((r) => r.fetchedAt),
  ]);
}

/** Election day before any covered state's polls close: people are still
 * voting, so /elections says polls are open — not "results", as if there
 * were some — and so does what search and link cards show of it. Needs a
 * live state (with none, `every` is vacuously true and the page would say
 * polls are open all day) and no count stored anywhere. */
export function everyLiveStateVoting(
  results: Pick<LiveResults, "phase" | "pollsClose" | "feeds" | "races" | "liveStates">,
  now: number
): boolean {
  return (
    showsResults(results.phase) &&
    results.phase.phase === "election_day" &&
    results.races.length === 0 &&
    results.liveStates.length > 0 &&
    results.liveStates.every((st) => pollsStillOpen(results, st, now))
  );
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
 * units are in, solid once the state lists its count as official. */
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
  /** Short tag for the feed: FLIP, LEAD, OFFICIAL COUNT, ALL IN, FIRST,
   * UPDATE. Never a bare OFFICIAL beside the leader's share, which reads as
   * a result. */
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
          // A state that gives no unit count still says every unit is in:
          // "All 0 precincts" would read as a count with nothing in it.
          d.totalUnits
            ? `All ${d.totalUnits.toLocaleString("en-US")} ${d.unitLabel ?? "precincts"} have reported`
            : "Every reporting unit is in",
          shares,
          "Counting can continue after every unit reports"
        ),
      };
    case "official":
      return {
        tag: "OFFICIAL COUNT",
        tone: "official",
        text: sentence("The state lists its count as official", shares, "Not called"),
      };
    case "flip":
      // Never "wins", even official: the state listing its count as
      // official is not a result (a Georgia general short of a majority
      // goes to a runoff), and Civitas never calls a race — the backend's
      // templates (live_results/bluesky.py) say the same.
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

const PARTY_NAME: Record<string, string> = {
  DEM: "Democrat",
  REP: "Republican",
  IND: "independent",
  LIB: "Libertarian",
  GRE: "Green",
  CON: "Constitution Party",
};

/** "Democrat leads", "Democrats lead" — for a map's accessible names. */
function leadsPhrase(party: string | null | undefined, plural = false): string {
  if (plural) {
    const holders = party && HOLDERS[party];
    return holders ? `${holders} lead` : "another party leads";
  }
  const name = party && PARTY_NAME[party];
  return name ? `${name} leads` : "another party leads";
}

/** How far one race's count is, in words: "official count", "25% in,
 * early", "80% in", or "" when the state gives no reporting figure. */
function progressPhrase(r: LiveRaceResult): string {
  if (r.official) return "official count";
  const share = reportingShare(r);
  if (share == null) return "";
  const pct = `${Math.round(100 * share)}% in`;
  return share < 0.5 ? `${pct}, early` : pct;
}

/** One race's standing in words, as its map fill says it: "no votes yet",
 * "tied, 40% in", "Republican leads, 80% in, seat changing party". Never
 * "wins": a race "leads" even once the state lists its count as official. */
export function raceStatusText(r: LiveRaceResult): string {
  if (!(r.votesCounted > 0)) return "no votes yet";
  const progress = progressPhrase(r);
  const head = isTied(r) ? "tied" : leadsPhrase(r.leaderParty);
  return [head, progress, r.flip ? "seat changing party" : ""].filter(Boolean).join(", ");
}

/** The opacity for a set of races led by one party, on resultFill's scale:
 * solid only when every one is official; faint (0.3) when any has fewer
 * than half its units in (one with nothing counted has none in); the
 * middle, unknown-progress value when a race gives no reporting figure;
 * otherwise by the least-counted race's share. For one race it is exactly
 * resultFill's. */
function aggregateOpacity(races: LiveRaceResult[]): number {
  if (races.every((r) => r.official && r.votesCounted > 0)) return 1;
  let min = 1;
  let unknown = false;
  for (const r of races) {
    if (r.official) continue;
    const share = r.votesCounted > 0 ? reportingShare(r) : 0;
    if (share == null) unknown = true;
    else min = Math.min(min, share);
  }
  if (min < 0.5) return 0.3;
  if (unknown) return 0.55;
  return 0.45 + 0.45 * min;
}

/** The party ahead across a set of races — whichever leads the most of
 * them, every party compared (a delegation led 3–1 by a third party is
 * that party's, not the one major party's that leads one seat). Null with
 * `tied` when two or more parties lead equally many, including a race or
 * pair of races that are level; null without it when nothing is counted. */
function pluralityLeader(races: LiveRaceResult[]): { party: string | null; tied: boolean } {
  const ranked = Object.entries(seatsLed(races)).sort((a, b) => b[1] - a[1]);
  if (!ranked.length) return { party: null, tied: races.some((r) => r.votesCounted > 0) };
  if (ranked[1] && ranked[1][1] === ranked[0][1]) return { party: null, tied: true };
  return { party: ranked[0][0], tied: false };
}

export interface StateShade {
  fill: string;
  /** The fill is a count that isn't being refreshed (`feedDown` with a
   * count to show): drawn with the stale stripe over it (STALE_SWATCH). */
  stale: boolean;
  /** The state's standing in words — the map's accessible name for it, so
   * the fill is never the only way to tell. Starts with the state code. */
  label: string;
}

/** A state on the national map, by chamber. One race (a Senate seat) is
 * drawn exactly as resultFill draws it. More than one — a House
 * delegation, or a state electing both its senators — is the party leading
 * the most of them, every party compared; tied or split when two or more
 * lead equally many. Fainter on the same scale as a single race, taken
 * from the least-counted of them: under half in anywhere is faint, and
 * solid only once every one is official. A count whose feed is down or
 * behind keeps that fill and is marked `stale`. */
export function stateShade(
  state: string,
  races: LiveRaceResult[],
  chamber: "S" | "H",
  covered: boolean,
  hasRace: boolean,
  /** The state's latest feed read failed (feedFailed) or the backend has
   * stopped reading it (stateFeedBehind): with no count to show, it is
   * drawn as FEED_FAILED_FILL, never as "no votes yet"; with one, `stale`. */
  feedDown = false,
  /** The state's polls are still open (pollsStillOpen): drawn as
   * POLLS_OPEN_FILL, which says nothing about the count. */
  pollsOpen = false
): StateShade {
  const what = chamber === "S" ? "Senate" : "House";
  const plain = (fill: string, label: string): StateShade => ({ fill, label, stale: false });
  if (!hasRace) return plain(UNCOVERED_FILL, `${state}: no Senate race this year`);
  const mine = races.filter((r) => r.office === chamber);
  if (!mine.length) {
    if (!covered) return plain(UNCOVERED_FILL, `${state}: no live count here`);
    if (pollsOpen) return plain(POLLS_OPEN_FILL, `${state}: polls not yet closed`);
    if (feedDown) return plain(FEED_FAILED_FILL, `${state}: results feed not read`);
    return plain(AWAITING_FILL, `${state} ${what}: no votes yet`);
  }
  // A count to colour, stale when its feed isn't being read.
  const stale = covered && feedDown;
  if (mine.length === 1) {
    return {
      fill: resultFill(mine[0], covered),
      label: `${state} ${what}: ${raceStatusText(mine[0])}`,
      stale,
    };
  }
  const { party, tied } = pluralityLeader(mine);
  let fill: string;
  if (party)
    fill = `rgba(${rgb(party === "OTHER" ? null : party)}, ${aggregateOpacity(mine).toFixed(2)})`;
  else fill = tied ? TIED_FILL : AWAITING_FILL;
  let label: string;
  if (chamber === "S") {
    // Two Senate races: each named, so a change of party in either shows.
    label = `${state} Senate: ${mine
      .map((r) => `${r.isSpecial ? "special" : "regular"} race ${raceStatusText(r)}`)
      .join("; ")}`;
  } else {
    const led = seatsLed(mine);
    const counted = mine.filter((r) => r.votesCounted > 0).length;
    const head = party
      ? `${leadsPhrase(party === "OTHER" ? null : party, true)} in the most seats`
      : tied
        ? "seats split evenly"
        : "no votes yet";
    const progress = mine.every((r) => r.official && r.votesCounted > 0)
      ? "every count official"
      : aggregateOpacity(mine) === 0.3
        ? "under half in for some seats"
        : "";
    const flips = mine.filter((r) => r.flip).length;
    label = [
      `${state} House: ${head}`,
      counted ? `seats led ${formatLed(led).replace(/ · /g, ", ")}, of ${mine.length} listed` : "",
      progress,
      flips ? `${flips} ${flips === 1 ? "seat" : "seats"} changing party` : "",
    ]
      .filter(Boolean)
      .join(", ");
  }
  return { fill, label, stale };
}

/** stateShade's fill alone. */
export function stateFill(
  races: LiveRaceResult[],
  chamber: "S" | "H",
  covered: boolean,
  hasRace: boolean,
  feedDown = false,
  pollsOpen = false
): string {
  return stateShade("", races, chamber, covered, hasRace, feedDown, pollsOpen).fill;
}
