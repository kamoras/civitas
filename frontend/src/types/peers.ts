import type { ReadoutMark } from "./readout";

/** Where a member stands among peers, measure by measure (backend
 *  app/peer_comparison.py). The frontend only places these figures. */
export type PeerGroupKey = "caucus" | "chamber";

export interface PeerMeasure {
  key: "smallGifts" | "pacMoney" | "breaks" | "billsMoved";
  label: string;
  /** The voter question the measure answers; rows sharing one are grouped. */
  question: string;
  /** What the figure is and why a voter might care: one fixed, neutral line. */
  why: string;
  /** How it is measured, for researchers. */
  measured: string;
  /** The span the figure covers: this Congress, or the member's most recent
   *  completed election (funding, flat between elections: never drawn over time). */
  window: "congress" | "election";
  /** Which way is higher, in words; the word is neutral about which is better. */
  direction: string;
  unit: "share" | "rate" | "count";
  value: number | null;
  /** The figure as printed ("13%", "0 of 493", "9 of 89"). */
  shown: string | null;
  expected: number | null;
  expectedBasis: "seat" | "peer median";
  median: number | null;
  /** label: the group ("Senate Republicans"); labelOne: one of them
   *  ("Senate Republican"), capitals kept. */
  peerGroup: { key: PeerGroupKey; label: string; labelOne?: string; n: number };
  /** Every peer's figure, ascending; ids and names in the same order. */
  distribution: number[];
  distributionIds: string[];
  distributionNames: string[];
  /** Each figure as printed ("2 of 480"), same order. */
  distributionShown: string[];
  /** Party and seat ("D-CA", "R-TX-5") and place on the row ("3rd highest of 47"), same order. */
  distributionSeats: string[];
  distributionRanks: string[];
  record: number | null;
  source: { name: string; url: string };
  percentile: number | null;
  word: string | null;
  sentence: string | null;
  thin: boolean;
  /** The strip's right end (on the measure's scale), its two ends as
   *  printed, and the tick's label ("11% seat average"); the internal
   *  /peers route's (peer_comparison.with_drawing). */
  axisMax: number;
  axisStartShown: string;
  axisEndShown: string;
  expectedShown: string | null;
  /** The measure night by night this Congress, once it has changed: each
   *  point placed 0-100 (x across, y down), its readout, and in words. */
  overTime: {
    points: { x: number; y: number }[];
    marks: ReadoutMark[];
    sentence: string;
  } | null;
}

export interface PeerComparison {
  group: PeerGroupKey;
  rule: { wellOutside: number[]; outside: number[]; wellGapSd: number; minRecord: number };
  measures: PeerMeasure[];
  standsOut: string;
  /** Why that line was picked: the rule, then the measure's why line. */
  standsOutWhy: string;
}

/** One member's figure on a whole-chamber strip (GET /api/congress/measures). */
export interface ChamberPoint {
  id: string;
  name: string;
  party: string;
  state: string;
  district: number | null;
  value: number;
  shown: string;
  expected: number | null;
  gap: number | null;
  /** The place on this measure's strip ("3rd highest of 100"), for its hover label. */
  rank: string;
}

export interface ChamberMeasure {
  key: PeerMeasure["key"];
  label: string;
  question: string;
  why: string;
  direction: string;
  unit: PeerMeasure["unit"];
  median: number | null;
  medianShown: string | null;
  axisMax: number;
  axisStartShown: string;
  axisEndShown: string;
  n: number;
  source: { name: string; url: string };
  points: ChamberPoint[];
  /** The three members furthest above, and below, the figure expected for them. */
  mostAbove: ChamberPoint[];
  mostBelow: ChamberPoint[];
}

export interface ChamberMember {
  id: string;
  name: string;
  party: string;
  caucus: string;
  state: string;
  district: number | null;
  /** House: the seat's Cook PVI on the sitting lines, positive leaning R. */
  lean: number | null;
  smallGifts: number | null;
  pacMoney: number | null;
  breaks: number | null;
  partyVotes: number | null;
  billsPastCommittee: number | null;
  bills: number | null;
  passed: number | null;
  law: number | null;
}

export interface ChamberMeasures {
  chamber: "senate" | "house";
  measures: ChamberMeasure[];
  members: ChamberMember[];
}
