export interface PoliticianCard {
  id: string;
  branch: "senate" | "house" | "president" | "scotus";
  name: string;
  party: string;
  state?: string;
  stateName?: string;
  district?: number | null;
  role: string;
  thumbnailUrl?: string | null;
  hasScorecard: boolean;
  overallScore?: number | null;
  activeIssueCount: number;
  isCurrent?: boolean;
  vacancyReason?: string | null;
  leftOfficeDate?: string | null;
}

export interface PoliticianIdentity {
  name: string;
  party: string;
  role: string;
  state?: string;
  stateName?: string;
  district?: number | null;
  yearsInOffice?: number;
  contactFormUrl?: string;
  websiteUrl?: string;
  officePhone?: string;
  officeAddress?: string;
  vacancyReason?: string | null;
  leftOfficeDate?: string | null;
  // President
  number?: number;
  termStart?: string;
  termEnd?: string | null;
  isCurrent?: boolean;
  // Justice
  appointingPresident?: string;
  dateStart?: string;
  thumbnailUrl?: string | null;
  isActive?: boolean;
}

export interface PoliticianActiveIssue {
  id: number;
  title: string;
  summary: string;
  rank: number;
  date: string;
  policyAreas: string[];
}

export interface GovernmentDoc {
  id: number;
  docType: string;
  title: string;
  date: string;
  url?: string;
  source?: string;
}

export interface GovernmentRecord {
  totalDocs: number;
  recentDocs: GovernmentDoc[];
}

export interface PoliticianProfile {
  id: string;
  branch: "senate" | "house" | "president" | "scotus";
  identity: PoliticianIdentity;
  hasScorecard: boolean;
  overallScore?: number | null;
  scorecard?: Record<string, unknown> | null;
  activeIssues: PoliticianActiveIssue[];
  governmentRecord: GovernmentRecord;
}

export interface PaginatedDocs {
  total: number;
  page: number;
  perPage: number;
  docs: (GovernmentDoc & { summary?: string; chamber?: string })[];
}
