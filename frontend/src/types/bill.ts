import type { PolicyAreaDetail } from "./senator";

export interface BillInFlight {
  billId: string;
  title: string;
  chamber: "senate" | "house";
  sponsorId: string;
  sponsorName: string;
  sponsorParty: "D" | "R" | "I";
  sponsorState: string;
  sponsorThumbnailUrl: string | null;
  introducedDate: string;
  latestAction: string;
  latestActionDate: string;
  stage: string;
  policyArea: string;
  congress: number;
  billType: string;
  isLaw: boolean;
  mentionCount: number;
}

export interface PaginatedBills {
  bills: BillInFlight[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
  stageCounts: Record<string, number>;
  /** Bills past introduction and referral among stageCounts. */
  inMotion: number;
  /** sort=hot only: bills named by a live Action Center issue, or, when no
   * issue names one, bills that moved since `movedSince`. */
  shown: "mentioned" | "moved" | null;
  movedSince: string | null;
}

export interface RelatedIssue {
  id: number;
  date: string;
  firstSurfaced: string;
  title: string;
  /** Possibly missing on a response cached before the field existed. */
  publicId?: string | null;
}

export interface BillDetail extends BillInFlight {
  policyAreas: PolicyAreaDetail[];
  partyLeaning: "R" | "D" | "bipartisan" | null;
  relatedIssues: RelatedIssue[];
  /** What happens to this Congress's bills at this stage (backend
   * bill_service.stage_base_rate); null for an earlier Congress's bill or
   * one whose outcome is known. */
  stageBaseRate?: {
    total: number;
    atStage: number;
    reachedStage: number;
    becameLaw: number;
    sentence: string;
  } | null;
  /** How far this Congress's bills have got, and this one
   * (bill_service.stage_milestones); null for an earlier Congress's bill. */
  stageMilestones?: StageMilestones | null;
}

export interface StageMilestones {
  /** The bills sitting members sponsored this Congress. */
  total: number;
  /** In order; each counts the bills that got at least that far. */
  milestones: { label: string; bills: number; share: number; shareText: string }[];
  /** The furthest milestone this bill has reached: an index into `milestones`. */
  reached: number;
}
