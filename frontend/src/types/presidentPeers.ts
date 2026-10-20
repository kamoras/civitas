/** GET /api/presidents/{id}/peers (backend app/president_peers.py): where a
 *  president stands among the other presidencies, figure by figure. Every
 *  figure, word and sentence is the backend's; the page only places them. */
export interface PresidentPeerMeasure {
  key: string;
  label: string;
  /** The question the figure answers; rows sharing one are grouped. */
  question: string;
  /** What the figure is and why a reader might care, in one plain line. */
  why: string;
  /** How it is measured, and what the tick is. */
  measured: string;
  source: { name: string; url: string };
  value: number;
  shown: string;
  expected: number | null;
  expectedBasis: "peer median" | "same start" | "same stretch";
  /** The tick's label, e.g. "median 51.3%" or "-7.5 points for the same start". */
  expectedShown: string | null;
  /** Who the president is set beside, and how many: the era a figure exists for. */
  peerGroup: { label: string; n: number };
  /** The other presidencies' figures, ascending; shown, ids and names in the same order. */
  distribution: number[];
  distributionShown: string[];
  distributionIds: string[];
  distributionNames: string[];
  percentile: number;
  /** The members' rule's word ("above other presidencies"); null when too few peers. */
  word: string | null;
  sentence: string;
  thin: boolean;
}

export interface PresidentPeers {
  rule: { wellOutside: number[]; outside: number[]; wellGapSd: number; minPeers: number };
  measures: PresidentPeerMeasure[];
  standsOut: string;
  standsOutWhy: string;
  /** Plain sentences for each record the presidency has none of. */
  missing: string[];
  historians: {
    rank: number | null;
    rated: number;
    points: number | null;
    surveyYear: number | null;
    surveyUrl: string | null;
  };
  recentApproval: number | null;
  otherTerms: { id: string; number: number; points: number }[];
}
