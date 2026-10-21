import type { ReadoutMark } from "./readout";

/** Always null since justice v3 (no justice is scored); kept for the
 *  API's shape, never shown. */
export interface JusticeScore {
  loyalty: number | null;
  overall: number | null;
}

/** Shown, not scored (backend justice_loyalty): how many points more often
 *  the justice sided with the federal government while the appointing
 *  president was in office, as a share (0.145 = 14.5 points), the
 *  justice's own estimate with its standard error and 95% confidence
 *  interval (computed by the API); the votes under the appointing president
 *  and under others, and the share of each for the government. */
export interface JusticeLoyalty {
  estimate: number;
  se: number;
  ciLow: number;
  ciHigh: number;
  votesIn: number;
  votesOut: number;
  rateIn: number;
  rateOut: number;
  throughTerm: number | null;
}

export interface Justice {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  dateStart: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  unanimousPct: number;
  authoredMajority: number;
  authoredDissent: number;
  authoredConcurrence: number;
  closeCaseMajorityPct: number;
  /** Agreement with each sitting justice, most first: the share of the
   *  cases both decided that they decided the same way. Optional only
   *  because a response cached before this field shipped can still be
   *  served; every live response carries it. */
  agreement?: { id: string; name: string; share: number }[];
  loyalty: JusticeLoyalty | null;
  /** Martin-Quinn position per term, [[term, position], ...], oldest first:
   *  shown, not scored. Negative is liberal, positive conservative. */
  idealPoints: [number, number][];
  /** The terms the voting record covers (those with decided cases stored),
   *  oldest first. Optional: a response cached before it shipped has none. */
  recordTerms?: number[];
  /** What the page draws, each figure beside the sitting justices'
   *  (backend services/justice_figures.py). Only on the profile's copy;
   *  absent from a response cached before it shipped. */
  figures?: JusticeFigures | null;
}

export interface JusticePeerPoint {
  id: string;
  name: string;
  value: number;
  shown: string;
  /** The place on the row ("2nd highest of 9", "Furthest left of 9"). */
  rank: string;
}

/** One figure on the record beside every sitting justice's. Every value,
 *  median and sentence is the API's; the page only places them. */
export interface JusticeMeasure {
  key: string;
  question: string;
  label: string;
  /** What the figure answers and why it matters: one plain line. */
  why: string;
  unit: "share" | "count" | "years" | "position";
  value: number | null;
  shown: string | null;
  record: number | null;
  /** Every justice with a figure, this one included, ascending. */
  peers: JusticePeerPoint[];
  median: number | null;
  medianShown: string | null;
  below: number | null;
  sentence: string;
  source: { name: string; url: string };
}

export interface JusticeInterval {
  id: string;
  name: string;
  estimate: number;
  ciLow: number;
  ciHigh: number;
  /** As printed: signed points ("+6.3", "\u22122.1 to +14.5"). */
  estimateShown: string;
  intervalShown: string;
}

export interface JusticeFigures {
  appointedBy: {
    id: string | null;
    name: string;
    party: string | null;
    year: number | null;
  } | null;
  peers: number;
  terms: number[];
  measures: JusticeMeasure[];
  appointer: {
    rows: JusticeInterval[];
    sentence: string;
    throughTerm: number | null;
    /** One readout per row, in order. */
    marks: ReadoutMark[];
  } | null;
}

/** A Supreme Court appointment a president made (GET
 *  /api/justices/appointed-by/{id}, from the FJC directory). `id` is the
 *  justice's page on the site, null when the site holds none. */
export interface AppointedJustice {
  id: string | null;
  name: string;
  /** "Associate Justice" or "Chief Justice", as the directory titles it. */
  seat: string;
  dateStart: string | null;
  dateEnd: string | null;
  isActive: boolean;
  photo: string | null;
}

/** A sitting justice, listed by seniority and not ranked (justice v3). */
export interface JusticeLeaderboardEntry {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  dateStart: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  loyalty: JusticeLoyalty | null;
}
