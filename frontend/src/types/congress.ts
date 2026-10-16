import type { NominationVote } from "@/types/nomination";
/** The Congress reports (backend/app/services/congress_service.py) and a
 * bill's record (backend/app/services/bill_record.py). Every count and
 * sentence here is computed by the backend. */

export type Chamber = "senate" | "house";

/** How a roll call's record reads (backend services/roll_call_shape.py):
 * which kind of vote, its count in words and what it decided. Optional for
 * a backend image older than these fields. */
export interface RollCallShape {
  /** "question" (yeas against nays), "election" (of the Speaker: each vote
   * names a candidate) or "quorum" (members answer present; nothing decided). */
  kind?: "question" | "election" | "quorum";
  /** "50–50, Vice President of the United States voted Yea", "433 present",
   * "Doe (TX) 218, Roe 215, Poe 1". */
  tally?: string;
  /** What was decided: the chamber's result, "Doe (TX) elected",
   * "No one elected" or "Quorum call". */
  resultLabel?: string;
  /** A Senate tie the Vice President broke; null otherwise. */
  tieBreaker?: { byWhom: string; vote: string } | null;
  /** An election's candidates, most votes first; [] otherwise. */
  candidates?: { name: string; votes: number }[];
}

export interface CongressEvent {
  /** The Congress the day fell in; a bill number names a different bill in each. */
  congress: number | null;
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

export interface RollCallSummary extends RollCallShape {
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
  /** Nominees counted from the Digest; routine promotion lists, whose size
   * it doesn't give, are counted apart in confirmedLists. */
  confirmed: number;
  confirmedLists: number;
  committeeMeetings: number;
}

/** no_record_published: the Congressional Record has no issue for the day
 * (it is published for every day either chamber is in session). */
export type ChamberStatus =
  "final" | "live" | "not_in_session" | "no_record" | "no_record_published";

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
  /** /api/congress/latest only: set when neither chamber did business on
   * the latest day (a pro forma sitting); what /congress leads with. */
  recess?: Recess | null;
}

export interface Recess {
  headline: string;
  sentence: string;
  lastBusinessDay: string | null;
  /** The Monday of the week to open: the last one with business. */
  week: string;
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
  congress: number | null;
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
  /** The neighbouring week or month, null past either end of the record. */
  previous: string | null;
  next: string | null;
}

export interface MonthReport extends PeriodReport {
  weeks: {
    week: string;
    start: string;
    end: string;
    sentence: string;
    totals: Record<Chamber, PeriodTotals>;
  }[];
}

export interface PartySplit {
  party: string;
  yea: number;
  nay: number;
  present: number;
  notVoting: number;
  /** An election of the Speaker: this party's votes for each candidate. */
  candidates?: Record<string, number>;
}

export interface VoteMember {
  lastName: string;
  firstName: string;
  party: string;
  state: string;
  position: string;
  bucket: "yea" | "nay" | "present" | "notVoting" | "candidate";
  /** An election of the Speaker: the candidate this member voted for. */
  candidate?: string | null;
  page: string | null;
  /** The member's own name, where the site has a page for them (a House
   * roll call prints only the Clerk's surname form); null otherwise. */
  name?: string | null;
  /** House only: the member's district (0 at-large); null in the Senate. */
  district?: number | null;
}

export interface VoteDetail extends RollCallSummary {
  /** The bill's short or popular title from Congress.gov, when the chamber's title isn't it. */
  billShortTitle?: string | null;
  /** What the vote decided about the nominations its question names; null
   *  for any other vote. Optional for a backend image older than this field. */
  nominationVote?: NominationVote | null;
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

/** Whether a part of a bill's page that comes from Civitas's own record is
 * held for the bill's Congress (bill_record.bill_coverage): an empty part
 * outside it is "not held", never "none". */
export interface CoveragePart {
  held: boolean;
  /** What isn't held and where it is, worded by the backend; null when held. */
  sentence: string | null;
}

/** What Civitas's record of Congress holds (GET /api/congress/coverage). */
export interface CongressCoverage {
  firstDay: string | null;
  firstCongress: number | null;
  rollCallCongresses: number[];
  sentence: string;
  rollCallSentence: string;
  congressionalRecordUrl: string;
  rollCallSources: Record<Chamber, string>;
}

export interface BillRecordVote extends RollCallShape {
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
  /** The roll call at its official source (Senate.gov or the House Clerk). */
  sourceUrl?: string;
  parties: PartySplit[];
}

/** One Record heading's speeches on a day (bill_record.floor_debate). */
export interface FloorDebate {
  date: string;
  chamber: Chamber;
  /** The Record's heading, verbatim. */
  heading: string;
  /** Each member once, at their first turn; `speeches` counts their turns. */
  speakers: { speaker: string; politicianId: string | null; speechId: number; speeches: number }[];
}

export interface BillRecord {
  billId: string;
  billLabel: string | null;
  congress: number;
  title: string | null;
  /** Congress.gov's short or popular title for the bill, verbatim, as cached; the h1 when present. */
  shortTitle?: string | null;
  /** Its title type as Congress.gov names it. */
  shortTitleType?: string | null;
  /** The official title from that same list. */
  officialTitle?: string | null;
  introducedDate: string | null;
  originChamber: string | null;
  policyArea: string | null;
  latestAction: { actionDate?: string; text?: string } | null;
  sponsors: BillPerson[];
  cosponsors: BillPerson[];
  cboCostEstimates: { title: string; description: string; pubDate: string; url: string }[];
  summary: { actionDesc: string; actionDate: string; paragraphs: string[] } | null;
  actions: {
    date: string;
    text: string;
    type: string;
    /** `sourceUrl`: the chamber's own record of the roll call (null when
     * the action doesn't say which), the link for a vote Civitas doesn't hold. */
    rollCalls: { chamber: string; number: number; session: number; sourceUrl?: string | null }[];
  }[];
  textVersions: { type: string; date: string; formats: Record<string, string> }[];
  votes: BillRecordVote[];
  /** Floor speeches whose Record heading names this bill, grouped by day,
   *  chamber and heading, newest first. Optional only for a backend older
   *  than the field. */
  floorDebate?: FloorDebate[];
  /** Speeches across floorDebate. */
  floorSpeechCount?: number;
  days: {
    date: string;
    chamber: Chamber;
    entries: { kind: string; text: string; source: string }[];
  }[];
  congressGovUrl: string;
  /** Parts Congress.gov could not return this time: shown as unavailable, never as empty. */
  unavailable: string[];
  /** Whether the votes, floor debate and Record days (Digest and committee
   * entries) are held for the bill's Congress. Optional for an older backend. */
  coverage?: { votes: CoveragePart; floor: CoveragePart; digest: CoveragePart };
}

/** A committee meeting or a report filed on a measure, as the Daily Digest
 * printed it (GET /api/congress/committee-record). */
export interface CommitteeEntry {
  id: number;
  kind: "meeting" | "report";
  chamber: "senate" | "house";
  date: string;
  /** The Congress the day fell in, for links to the bills it names. */
  congress: number | null;
  /** A meeting's committee as the Digest names it; null for a report. */
  committee: string | null;
  text: string;
  pages: string;
  /** "H. Rept. 119-851"; null when the entry names none. */
  report: string | null;
  bills: { billId: string; billLabel: string }[];
}

export interface CommitteeRecordPage {
  entries: CommitteeEntry[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
  /** The bill the search was read as, when it was one. */
  bill: { billId: string; billLabel: string } | null;
}

export interface CommitteeSummary {
  chamber: "senate" | "house";
  name: string;
  meetings: number;
  latest: string;
}
