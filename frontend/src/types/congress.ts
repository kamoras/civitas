/** The Congress reports (backend/app/services/congress_service.py) and a
 * bill's record (backend/app/services/bill_record.py). Every count and
 * sentence here is computed by the backend. */

export type Chamber = "senate" | "house";

export interface CongressEvent {
  kind: "passed" | "failed" | "reported" | "confirmed" | "committee" | "floor";
  name: string;
  /** The record's own wording. */
  text: string;
  billId: string | null;
  billLabel: string | null;
  isResolution: boolean;
  /** For a passed bill: "both" chambers have now passed it, or the chamber it goes to next. */
  nextStep: "both" | Chamber | null;
  time: string | null;
  pages: string;
  date: string;
  chamber: Chamber;
}

export interface RollCallSummary {
  chamber: Chamber;
  congress: number;
  session: number;
  number: number;
  date: string;
  question: string;
  title: string;
  result: string;
  rejected: boolean | null;
  majorityRequirement: string;
  yeas: number;
  nays: number;
  present: number;
  notVoting: number;
  billId: string | null;
  billLabel: string | null;
  sourceUrl: string;
}

export interface DayCounts {
  recordVotes: number;
  billsPassed: number;
  resolutionsPassed: number;
  failed: number;
  reported: number;
  confirmed: number;
  committeeMeetings: number;
}

/** no_record_published: the Congressional Record has no issue for the day
 * (it is published for every day either chamber is in session). */
export type ChamberStatus = "final" | "live" | "not_in_session" | "no_record" | "no_record_published";

export interface ChamberDay {
  chamber: Chamber;
  status: ChamberStatus;
  convenedAt: string | null;
  adjournedAt: string | null;
  minutesInSession: number | null;
  adjournmentText: string;
  nextMeeting: string | null;
  nextProgram: string;
  billsIntroduced: number | null;
  resolutionsIntroduced: number | null;
  introducedText: string;
  source: "digest" | "floor_log" | null;
  sourceUrl: string | null;
  fetchedAt: string | null;
  floorLogStatus: "ok" | "absent" | "failed" | null;
  counts: DayCounts;
  sentence: string;
  votes: RollCallSummary[];
  passed: CongressEvent[];
  failed: CongressEvent[];
  reported: CongressEvent[];
  confirmed: CongressEvent[];
  committees: CongressEvent[];
  floorLog: CongressEvent[];
}

export interface DayReport {
  date: string;
  sentence: string;
  chambers: Record<Chamber, ChamberDay>;
  previousDay: string | null;
  nextDay: string | null;
  syncedAt: string | null;
}

export interface PeriodTotals extends DayCounts {
  daysInSession: number;
  billsIntroduced: number;
  resolutionsIntroduced: number;
  daysPending: number;
}

export interface PeriodDay {
  date: string;
  /** No Congressional Record was published for this day. */
  noRecordPublished: boolean;
  senate: PeriodChamberDay;
  house: PeriodChamberDay;
}

export interface PeriodChamberDay {
  inSession: boolean;
  recorded: boolean;
  convenedAt: string | null;
  adjournedAt: string | null;
  minutesInSession: number | null;
  counts: DayCounts;
}

export interface BecameLaw {
  billId: string;
  billLabel: string | null;
  name: string;
  date: string;
  text: string;
}

export interface PeriodReport {
  start: string;
  end: string;
  sentence: string;
  totals: Record<Chamber, PeriodTotals>;
  days: PeriodDay[];
  passedBothChambers: CongressEvent[];
  passedOneChamber: CongressEvent[];
  closestVotes: RollCallSummary[];
  votes: RollCallSummary[];
  becameLaw: BecameLaw[];
  syncedAt: string | null;
  previous: string;
  next: string;
}

export interface MonthReport extends PeriodReport {
  weeks: { week: string; start: string; end: string; sentence: string; totals: Record<Chamber, PeriodTotals> }[];
}

export interface PartySplit {
  party: string;
  yea: number;
  nay: number;
  present: number;
  notVoting: number;
}

export interface VoteMember {
  lastName: string;
  firstName: string;
  party: string;
  state: string;
  position: string;
  bucket: "yea" | "nay" | "present" | "notVoting";
  page: string | null;
}

export interface VoteDetail extends RollCallSummary {
  parties: PartySplit[];
  members: VoteMember[];
}

export interface BillPerson {
  name: string;
  party: string;
  state: string;
  district: number | null;
  bioguideId: string | null;
  isOriginalCosponsor: boolean | null;
  sponsorshipDate: string | null;
  page: string | null;
}

export interface BillRecordVote {
  chamber: Chamber;
  session: number;
  number: number;
  date: string;
  question: string;
  title: string;
  result: string;
  rejected: boolean | null;
  yeas: number;
  nays: number;
  present: number;
  notVoting: number;
  parties: PartySplit[];
}

export interface BillRecord {
  billId: string;
  billLabel: string | null;
  congress: number;
  title: string | null;
  introducedDate: string | null;
  originChamber: string | null;
  policyArea: string | null;
  latestAction: { actionDate?: string; text?: string } | null;
  sponsors: BillPerson[];
  cosponsors: BillPerson[];
  cboCostEstimates: { title: string; description: string; pubDate: string; url: string }[];
  summary: { actionDesc: string; actionDate: string; paragraphs: string[] } | null;
  actions: { date: string; text: string; type: string; rollCalls: { chamber: string; number: number; session: number }[] }[];
  textVersions: { type: string; date: string; formats: Record<string, string> }[];
  votes: BillRecordVote[];
  days: { date: string; chamber: Chamber; entries: { kind: string; text: string; source: string }[] }[];
  congressGovUrl: string;
  /** Parts Congress.gov could not return this time: shown as unavailable, never as empty. */
  unavailable: string[];
}
