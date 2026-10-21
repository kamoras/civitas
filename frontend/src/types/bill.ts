import type { PolicyAreaDetail } from "./senator";

export interface BillInFlight {
  billId: string;
  /** The sponsor's listing title (the official one for a bill introduced without a short title). */
  title: string;
  /** The short or popular title from Congress.gov's titles list, verbatim, when the listing title isn't it. */
  shortTitle?: string | null;
  /** Its title type as Congress.gov names it ("Short Title(s) as Passed House"). */
  shortTitleType?: string | null;
  /** The official title from that same list, shown under the short title. */
  officialTitle?: string | null;
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
  /** The funnel's drawing per stage: share of every bill counted ("16%")
   * and the bar's width against the fullest stage (bill_service.stage_flow). */
  stageFlow: Record<string, { shareText: string; barPercent: number }>;
  /** Bills past introduction and referral among stageCounts. */
  inMotion: number;
  /** sort=hot only: bills named by a live Action Center issue, or, when no
   * issue names one, bills that moved since `movedSince`. */
  shown: "mentioned" | "moved" | null;
  movedSince: string | null;
  /** reached= only: the heading of that milestone's list
   * ("Bills that became law this Congress"). */
  reachedLabel?: string | null;
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

export interface StageMilestone {
  label: string;
  /** Its stage code: /congress/bills?reached=<stage> lists exactly `bills`. */
  stage: string;
  bills: number;
  share: number;
  shareText: string;
}

export interface StageMilestones {
  /** The bills sitting members sponsored this Congress. */
  total: number;
  /** In order; each counts the bills that got at least that far. */
  milestones: StageMilestone[];
  /** The furthest milestone this bill has reached: an index into `milestones`. */
  reached: number;
  /** This bill's exact stage code and its name. */
  stage?: string | null;
  stageLabel?: string | null;
  /** Other bills at exactly that stage (/congress/bills?stage=<stage> less
   * this one); null once the bill is law or vetoed. */
  stageOthers?: number | null;
}
