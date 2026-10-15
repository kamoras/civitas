/** Presidential nominations (backend services/nomination_service.py). */

import type { PartySplit, VoteMember } from "@/types/congress";

export type NominationStatus = "pending" | "confirmed" | "returned" | "withdrawn";
export type NominationKind = "civilian" | "judicial" | "military";

export interface NominationSummary {
  congress: number;
  citation: string;
  /** Congress.gov's description, verbatim: the nominee, their state, the office. */
  description: string;
  organization: string;
  positionTitle: string;
  kind: NominationKind;
  /** The first few nominees as published: "Jane Q. Doe (TX)". */
  nominees: string[];
  nomineeCount: number;
  receivedDate: string;
  status: NominationStatus;
  statusDate: string | null;
  latestAction: { date: string; text: string };
  daysToConfirm: number | null;
}

export type StatusCounts = Record<NominationStatus, number>;

export interface NominationList {
  congress: number;
  total: number;
  page: number;
  perPage: number;
  /** By status, under every filter but status. */
  counts: StatusCounts;
  items: NominationSummary[];
}

export interface ConfirmDistribution {
  n: number;
  median: number | null;
  p25: number | null;
  p75: number | null;
  bins: { start: number; end: number; count: number }[];
}

export interface NominationStats {
  congress: number;
  total: number;
  counts: StatusCounts;
  kinds: Record<
    NominationKind,
    {
      nominations: number;
      people: number;
      counts: StatusCounts;
      timeToConfirm: ConfirmDistribution;
    }
  >;
  binDays: number;
  organizations: { name: string; count: number }[];
  asOf: string | null;
}

export interface NominationPosition {
  ordinal: number;
  positionTitle: string;
  organization: string;
  introText: string;
  nomineeCount: number;
  names: { firstName: string; middleName: string; lastName: string; state: string }[];
}

export interface NominationDetail extends Omit<NominationSummary, "nominees"> {
  nominees: NominationPosition[];
  actions: { date: string; code: string; type: string; text: string }[];
  hearings: { citation: string; jacketNumber: number | null; date: string; url: string | null }[];
  /** An action says a hearing was held (its transcript may not be listed yet). */
  hearingHeld: boolean;
  committee: { code: string; name: string } | null;
  fjc: { judgeId: string; url: string } | null;
  congressGovUrl: string;
  rollCalls: {
    chamber: "senate";
    congress: number;
    session: number;
    number: number;
    date: string;
    question: string;
    result: string;
    rejected: boolean | null;
    yeas: number;
    nays: number;
    present: number;
    notVoting: number;
    sourceUrl: string;
    /** How many nominations the question names (more than one: en bloc). */
    citations: number;
    /** Each party's split, on the vote to confirm only. */
    parties: PartySplit[] | null;
  }[];
  finalVote: { session: number; number: number; members: VoteMember[] } | null;
  updateDate: string;
}

/** A nomination a roll call's question names (VoteDetail, VoteRollCall). */
export interface RollCallNomination {
  citation: string;
  congress: number;
  positionTitle: string;
  organization: string;
  status: NominationStatus;
  nominees: string[];
  nomineeCount: number;
}
