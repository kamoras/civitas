export interface RelatedExploreDoc {
  id: number;
  title: string;
  docType: string;
  date: string;
  url: string | null;
  commentUrl?: string | null;
  commentsCloseOn?: string | null;
}

export interface RelatedSenator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  overallScore: number;
  leadershipScore: number | null;
  chamber?: "senate" | "house";
  matchReason?: string | null;
  contactFormUrl?: string | null;
  websiteUrl?: string | null;
}

export interface ActionItem {
  text: string;
  type: string;
  url?: string | null;
}

export interface RelatedBill {
  name: string;
  id: string;
  url: string;
  /** Path to our own bill page ("/bills/HR.22") when we host this bill. */
  internalUrl?: string | null;
}

export interface ActionIssue {
  id: number;
  /** Shown to readers and used in share links — not the raw autoincrement id. */
  publicId: string;
  /** Bumped to today on every pipeline run that re-matches this story to
   *  fresh coverage, whether or not anything changed — the day this
   *  appears under, not when it happened. Use `firstSurfaced` for that. */
  date: string;
  /** When this story was first surfaced (row created), fixed forever after. */
  firstSurfaced: string;
  rank: number;
  title: string;
  summary: string;
  facts: string[];
  /** Subset of `facts` not present as of this issue's last genuine content
   *  change — empty for an issue that's never been updated. */
  newFacts: string[];
  actions: ActionItem[];
  sourceUrls: string[];
  sourceNames: string[];
  policyAreas: string[];
  relatedBills: RelatedBill[];
  relatedExploreDocs: RelatedExploreDoc[];
  relatedSenators: RelatedSenator[];
  relatedMonitorSlugs?: string[];
  concernedCount: number;
  notPriorityCount: number;
  fullStory?: string | null;
  /** Only ever true from the issues-list fetch — the single-issue lookup
   *  has no peer issues to judge traction against. */
  isTrending: boolean;
}

export interface MyRepSenator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  initials: string;
  scores: {
    fundingIndependence: number;
    promisePersistence: number;
    independentVoting: number;
    fundingDiversity: number;
    legislativeEffectiveness: number;
    overall: number;
  };
  leadershipScore: number | null;
  ideologyScore: number | null;
  yearsInOffice: number;
  contactFormUrl?: string | null;
  officePhone?: string | null;
  websiteUrl?: string | null;
  connectedIssues: { id: number; rank: number; title: string; policyAreas: string[] }[];
}

export interface MyRepRep {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  district: number;
  initials: string;
  scores: {
    fundingIndependence: number;
    promisePersistence: number;
    independentVoting: number;
    fundingDiversity: number;
    legislativeEffectiveness: number;
    overall: number;
  };
  leadershipScore: number | null;
  ideologyScore: number | null;
  yearsInOffice: number;
  contactFormUrl?: string | null;
  officePhone?: string | null;
  websiteUrl?: string | null;
  connectedIssues: { id: number; rank: number; title: string; policyAreas: string[] }[];
}

export interface MyRepsResponse {
  state: string;
  senators: MyRepSenator[];
  representatives: MyRepRep[];
  issueDate: string | null;
}

export interface ActionIssuesResponse {
  date: string | null;
  issues: ActionIssue[];
  availableDates?: string[];
  generatedAt?: string;
}
