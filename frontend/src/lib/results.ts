/**
 * Live election results: colour, labels and wording for the results pages.
 *
 * Every sentence here is a fixed frame around the state's own numbers,
 * matching the backend's templates (analyze/election_results_bluesky.py):
 * a race is "leading" until the state itself calls its count official, and
 * nothing on the page projects or calls a winner.
 */

import type { ElectionPhaseInfo, LiveRaceResult, ResultEvent, ResultEventPerson } from "@/types/election";

const DEM = "130,172,255"; // dem-blue #82acff
const REP = "255,137,137"; // rep-red #ff8989
const OTHER = "201,149,255"; // ind-purple #c995ff

/** Fill for a state or district with no count yet, in a state read live. */
export const AWAITING_FILL = "#2a2520";
/** Fill for a place this page has no live count for at all. */
export const UNCOVERED_FILL = "rgba(255, 255, 255, 0.05)";

/** Whether the page should lead with results rather than research. A
 * missing phase (an older backend mid-rollout) is the campaign page. */
export function showsResults(phase: ElectionPhaseInfo | null | undefined): boolean {
  return !!phase && phase.phase !== "campaign";
}

/** Share of reporting units in, 0..1, or null when the state gives none. */
export function reportingShare(r: { reportingUnits: number | null; totalUnits: number | null }): number | null {
  if (!r.totalUnits || r.reportingUnits == null) return null;
  return Math.min(1, r.reportingUnits / r.totalUnits);
}

function rgb(party: string | null): string {
  if (party === "DEM") return DEM;
  if (party === "REP") return REP;
  return OTHER;
}

/** A race's map fill: its leader's party, paler while fewer than half the
 * units are in, solid once the state calls it official. */
export function resultFill(result: LiveRaceResult | undefined, covered: boolean): string {
  if (!result) return covered ? AWAITING_FILL : UNCOVERED_FILL;
  if (!result.leaderParty) return AWAITING_FILL;
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

const LETTER: Record<string, string> = { DEM: "D", REP: "R", IND: "I", LIB: "L", GRE: "G", CON: "C" };
const HOLDERS: Record<string, string> = {
  DEM: "Democrats", REP: "Republicans", IND: "independents", LIB: "Libertarians", GRE: "Greens",
};

export function partyLetter(party: string | null | undefined): string {
  return party ? LETTER[party] ?? party : "";
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
export function raceLabel(r: { state: string; office: string; district: number | null; isSpecial?: boolean }): string {
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
  const shares = [share(d.leader), share(d.runnerUp)].filter(Boolean).join(", ");
  const reporting = reportingText(d);
  const holders = d.heldBy ? HOLDERS[d.heldBy] ?? d.heldBy : "another party";
  switch (event.kind) {
    case "first_returns":
      return { tag: "FIRST", tone: "neutral", text: sentence("First returns", reporting, shares) };
    case "lead_change":
      return {
        tag: "LEAD",
        tone: "lead",
        text: sentence(
          `${withParty(d.leader)} moves ahead of ${withParty(d.previousLeader ?? null)}`,
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
      return { tag: "OFFICIAL", tone: "official", text: sentence("The state lists its count as official", shares) };
    case "flip":
      return {
        tag: "FLIP",
        tone: "flip",
        text: d.official
          ? sentence(`${withParty(d.leader)} wins in the official count, taking a seat ${holders} held`, shares)
          : sentence(`${withParty(d.leader)} leads in a seat ${holders} hold`, shares, reporting, "Not final"),
      };
    case "flip_reversed":
      return {
        tag: "UPDATE",
        tone: "lead",
        text: sentence(
          `${withParty(d.leader)} is ahead again, so the seat no longer shows a change of party`,
          reporting
        ),
      };
    default:
      return { tag: "UPDATE", tone: "neutral", text: sentence(shares, reporting) };
  }
}

/** "9:42 PM ET" — election night is told in Eastern time, as every
 * network does, whatever the reader's own zone. */
export function formatEasternTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
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
  const houseLeads: Record<string, number> = {};
  for (const r of house) if (r.leaderParty) houseLeads[r.leaderParty] = (houseLeads[r.leaderParty] ?? 0) + 1;
  return { senate, house, houseLeads, flips: races.filter((r) => r.flip).length };
}

/** Seats led, by party, over a set of races. */
export function seatsLed(races: LiveRaceResult[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of races) if (r.leaderParty) out[r.leaderParty] = (out[r.leaderParty] ?? 0) + 1;
  return out;
}

/** The fill for a state on the national map, by chamber: its Senate race's
 * leader, or — for House — the party leading more of its seats (paler for
 * a split delegation). */
export function stateFill(
  races: LiveRaceResult[],
  chamber: "S" | "H",
  covered: boolean,
  hasRace: boolean
): string {
  if (!hasRace) return UNCOVERED_FILL;
  const mine = races.filter((r) => r.office === chamber);
  if (chamber === "S") return resultFill(mine[0], covered);
  if (!mine.length) return covered ? AWAITING_FILL : UNCOVERED_FILL;
  const leads = seatsLed(mine);
  const d = leads.DEM ?? 0;
  const r = leads.REP ?? 0;
  if (!d && !r) return AWAITING_FILL;
  if (d === r) return "rgba(205, 199, 188, 0.45)";
  const party = d > r ? "DEM" : "REP";
  const margin = Math.abs(d - r) / (d + r);
  return `rgba(${rgb(party)}, ${(0.35 + 0.55 * margin).toFixed(2)})`;
}
