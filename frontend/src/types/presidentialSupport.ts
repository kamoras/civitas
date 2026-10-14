/** "Votes with the president's stated position": every number is computed by
 * the backend (services/presidential_support_service.py). */

export type ComparedWithExpected = "more" | "less" | "within";

export interface SupportNumbers {
  withPresident: number;
  votes: number;
  rate: number | null;
  rateLow: number | null;
  rateHigh: number | null;
  expected: number | null;
  difference: number | null;
  differenceLow: number | null;
  differenceHigh: number | null;
  comparedWithExpected: ComparedWithExpected | null;
}

export interface PolicyStatement {
  title: string;
  url: string;
  issued: string;
  position: "support" | "oppose";
  sentence: string;
}

export interface SupportRollCall {
  chamber: "senate" | "house";
  congress: number;
  session: number;
  rollCall: number;
  date: string;
  question: string;
  title: string;
  billId: string | null;
  result: string;
  sourceUrl: string;
  presidentSide: "Yea" | "Nay";
  statement: PolicyStatement;
  /** Member view only: the member's recorded position, null when none. */
  memberVote?: string | null;
  /** Member view only: null when the position is not a vote either way. */
  withPresident?: boolean | null;
}

export interface SupportFit {
  a: number;
  b: number;
  bOpposed: number;
  members: number;
  votes: number;
}

export interface SupportMethod {
  source: string;
  sourceUrl: string;
  counted: string;
  position: string;
  expected: string;
  interval: string;
  neutral: string;
  fits: Record<string, SupportFit>;
  /** Per party, [seat lean (PVI, + leans Republican), expected rate]. */
  curves: Record<string, [number, number][]>;
  computedAt: string | null;
}

export interface ChamberSupportMember extends SupportNumbers {
  id: string;
  name: string;
  party: string;
  state: string;
  district: number | null;
  seatLean: number | null;
}

export interface ChamberSupport {
  chamber: "senate" | "house";
  congress: number;
  members: ChamberSupportMember[];
  rollCalls: SupportRollCall[];
  method: SupportMethod;
}

export interface MemberSupport extends SupportNumbers {
  id: string;
  name: string | null;
  chamber: "senate" | "house";
  congress: number;
  party: string;
  seatLean: number | null;
  rollCalls: SupportRollCall[];
  method: SupportMethod;
}
