/** Where a member stands among peers, measure by measure (backend
 *  app/peer_comparison.py). The frontend only places these figures. */
export type PeerGroupKey = "caucus" | "chamber";

export interface PeerMeasure {
  key: "smallGifts" | "pacMoney" | "breaks" | "billsMoved";
  label: string;
  /** Which way is higher, in words; the word is neutral about which is better. */
  direction: string;
  unit: "share" | "rate" | "count";
  value: number | null;
  /** The figure as printed ("13%", "0 of 493", "9 of 89"). */
  shown: string | null;
  expected: number | null;
  expectedBasis: "seat" | "peer median";
  median: number | null;
  peerGroup: { key: PeerGroupKey; label: string; n: number };
  /** Every peer's figure, ascending; ids and names in the same order. */
  distribution: number[];
  distributionIds: string[];
  distributionNames: string[];
  record: number | null;
  source: { name: string; url: string };
  percentile: number | null;
  word: string | null;
  sentence: string | null;
  thin: boolean;
}

export interface PeerComparison {
  group: PeerGroupKey;
  rule: { wellOutside: number[]; outside: number[]; wellGapSd: number; minRecord: number };
  measures: PeerMeasure[];
  standsOut: string;
}
