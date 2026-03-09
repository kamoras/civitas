export interface RelatedExploreDoc {
  id: number;
  title: string;
  docType: string;
  date: string;
  url: string | null;
}

export interface RelatedSenator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  overallScore: number;
  leadershipScore: number | null;
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
}
