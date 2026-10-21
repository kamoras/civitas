/** GET /api/front-page (backend/app/api/notable.py): the homepage's front
 *  page, "Entered into the record". Which entry leads, which two make the
 *  second tier and what each column lists is the backend's fixed rule, and
 *  every sentence and figure is the backend's; the page only draws them. */

import type { AmendmentFields } from "./congress";
import type { CountdownFigure, DotsFigure, Figure, SplitFigure, StatFigure } from "./figure";

export interface FrontPageVote extends AmendmentFields {
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
  tieBreaker: { byWhom: string; vote: string } | null;
}

interface EntryBase {
  column: "congress" | "agencies" | "news";
  /** The day the entry is filed under (a comment period's: its deadline). */
  date: string;
  headline: string;
  figure: Figure | null;
}

/** The week's closest simple-majority vote. */
export interface ClosestEntry extends EntryBase {
  kind: "closest";
  vote: FrontPageVote;
  figure: SplitFigure;
}

/** The one member of a party who voted the other way from all the rest. */
export interface LoneBreakEntry extends EntryBase {
  kind: "loneBreak";
  vote: FrontPageVote;
  /** Civitas page id, or null for a member the site has no page for. */
  memberId: string | null;
  memberName: string;
  party: "D" | "R";
  state: string;
  /** The member's state's other senator, for a side-by-side; null otherwise. */
  compareWithId: string | null;
  compareWithName: string | null;
  figure: DotsFigure;
}

/** A bill that became law. */
export interface LawEntry extends EntryBase {
  kind: "law";
  /** Congress.gov's title of the bill. */
  dek: string;
  /** The action as recorded ("Became Public Law No: 119-68."). */
  action: string;
  billId: string;
  billLabel: string | null;
  congress: number;
}

/** A nomination the Senate confirmed. */
export interface ConfirmedEntry extends EntryBase {
  kind: "confirmed";
  /** The position as Congress.gov names it. */
  dek: string | null;
  congress: number;
  citation: string;
  vote: FrontPageVote | null;
}

/** An open comment period. */
export interface CommentEntry extends EntryBase {
  kind: "comment";
  agencyName: string;
  agencySlug: string | null;
  docType: string;
  id: number;
  key: string;
  commentUrl: string;
  figure: CountdownFigure | null;
}

/** A story in the news, one per story: its newest day, the others folded. */
export interface IssueEntry extends EntryBase {
  kind: "issue";
  publicId: string;
  /** The outlet whose headline this is. */
  outlet: string | null;
  outlets: string[];
  /** The story's other days, folded under this one. */
  alsoDates: string[];
  /** The national monitor the story belongs to. */
  monitor: { slug: string; title: string } | null;
  figure: StatFigure | null;
}

export type FrontPageEntry =
  ClosestEntry | LoneBreakEntry | LawEntry | ConfirmedEntry | CommentEntry | IssueEntry;

export interface FrontPage {
  /** The Eastern date the page is for. */
  asOf: string;
  /** The nameplate's dateline, one phrase each: the date, Congress, the election. */
  dateline: string[];
  edition: { sentence: string; week: string | null; weekLabel: string | null };
  /** Monday of the latest week of business, which the Congress entries come from; null before any. */
  voteWeek: string | null;
  lead: FrontPageEntry | null;
  tier: FrontPageEntry[];
  columns: Record<"congress" | "agencies" | "news", FrontPageEntry[]>;
}
