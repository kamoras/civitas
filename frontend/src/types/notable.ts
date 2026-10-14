/** GET /api/notable (backend/app/api/notable.py): the homepage's "On the
 *  record" entries. Every figure and sentence is the backend's. */

export interface NotableVote {
  chamber: "senate" | "house";
  congress: number;
  session: number;
  number: number;
  date: string;
  question: string;
  title: string;
  result: string;
  yeas: number;
  nays: number;
  billId: string | null;
  billLabel: string | null;
}

interface NotableBase {
  sentence: string;
  vote: NotableVote;
}

/** The week's closest simple-majority vote. */
export interface ClosestVoteEntry extends NotableBase {
  kind: "closest";
}

/** The one member of a party who voted the other way from all the rest. */
export interface LoneBreakEntry extends NotableBase {
  kind: "loneBreak";
  /** Civitas page id, or null for a member the site has no page for. */
  memberId: string | null;
  memberName: string;
  party: "D" | "R";
  state: string;
  /** The member's state's other senator, for a side-by-side; null otherwise. */
  compareWithId: string | null;
  compareWithName: string | null;
}

export type NotableEntry = ClosestVoteEntry | LoneBreakEntry;

export interface Notable {
  /** Monday and Sunday of the week shown; null when there is none. */
  weekStart: string | null;
  weekEnd: string | null;
  entries: NotableEntry[];
}
