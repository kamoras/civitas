/** GET /api/this-week (backend/app/api/this_week.py): the "this week"
 *  strip's data. Every figure and sentence is the backend's. */

export interface ThisWeekChamber {
  chamber: "senate" | "house";
  inSessionToday: boolean;
  daysInSessionThisWeek: number;
  recordVotesThisWeek: number;
  /** YYYY-MM-DD, or null when no notice of the next meeting is on record. */
  nextMeeting: string | null;
}

export interface ThisWeekVote {
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
}

export interface CommentDeadline {
  id: number;
  title: string;
  docType: string;
  agencyName: string | null;
  commentsCloseOn: string;
  commentUrl: string;
  daysLeft: number;
  /** Public id of the Action Center issue that cites the rule, if any. */
  issueId: string | null;
  /** How many other documents cite it (0 when that isn't why it is listed). */
  citedBy: number;
}

export interface ThisWeek {
  election: {
    date: string;
    phase: "campaign" | "election_day" | "results";
    daysUntil: number;
    states: string[];
  };
  congress: {
    weekStart: string;
    sentence: string;
    chambers: Record<"senate" | "house", ThisWeekChamber>;
    recentVotes: ThisWeekVote[];
  };
  commentDeadlines: CommentDeadline[];
}
