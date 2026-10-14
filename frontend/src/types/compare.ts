/** GET /api/compare/agreement (backend/app/api/compare.py): how often two
 *  members of one chamber voted the same way this Congress. Every figure
 *  and sentence is the backend's. */

export interface AgreementSplit {
  chamber: "senate" | "house";
  congress: number;
  session: number;
  number: number;
  date: string;
  question: string;
  title: string;
  result: string;
  billId: string | null;
  billLabel: string | null;
  /** Each member's vote, as "Yea" or "Nay". */
  left: "Yea" | "Nay";
  right: "Yea" | "Nay";
}

export interface Agreement {
  chamber: "senate" | "house";
  congress: number;
  /** Roll calls on which both cast a Yea or a Nay. */
  bothVoted: number;
  agreed: number;
  split: number;
  /** Whole percent; null when they share no vote. */
  agreementPct: number | null;
  sentence: string;
  /** The latest votes they split on, newest first. */
  recentSplits: AgreementSplit[];
}
