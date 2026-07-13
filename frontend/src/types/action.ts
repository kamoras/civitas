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
}

export interface ActionIssue {
  id: number;
  date: string;
  rank: number;
  title: string;
  summary: string;
  facts: string[];
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

export interface DailyTheme {
  tagline: string;
  mood: string;
  accent: string;
  accentAlt: string;
  glowIntensity: number;
  animationSpeed: number;
  borderStyle: string;
  heroGradient: string[];
  customCSS?: string;
}

export interface ActionIssuesResponse {
  date: string | null;
  issues: ActionIssue[];
  theme?: DailyTheme | null;
  availableDates?: string[];
  generatedAt?: string;
}
