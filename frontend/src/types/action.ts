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
  /** Path to our own bill page ("/congress/bills/HR.22") when we host this bill. */
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
  /** Source name per fact, aligned with `facts` by index. Facts are
   * verbatim spans of real reporting, so this is what lets a reader
   * check one against the outlet that made it. Empty for issues that
   * predate the claim layer. */
  factSources: string[];
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
  fullStory?: string | null;
  /** Only ever true from the issues-list fetch — the single-issue lookup
   *  has no peer issues to judge traction against. */
  isTrending: boolean;
  /** "developing" is a primary-source-only draft awaiting press
   *  corroboration (see backend early_signal.py) — shown with a
   *  disclosure badge and ranked after every "confirmed" issue. */
  status: "developing" | "confirmed";
  /** What a developing issue was drafted from — "senate_roll_call_vote",
   *  "house_roll_call_vote", "federal_register_significant_rule",
   *  "election_results" — null for news-derived issues. Optional for an
   *  older backend mid-rollout. */
  sourceType?: string | null;
  /** A seat-flip issue's count: when its figures were read (UTC ISO) and
   * whether the state calls it official. Null otherwise; optional for an
   * older backend. */
  countAsOf?: string | null;
  countOfficial?: boolean | null;
  /** Only ever set from a source article whose feed explicitly granted
   *  redistribution rights (see backend news_feeds._rights_cleared_image)
   *  — null for the large majority of issues. Used for the OG image and
   *  the in-page renders on the Action Center and issue pages. */
  imageUrl?: string | null;
  /** The source's own photo caption — real accessible alt text, not a
   *  generic fallback. Empty when the source supplied none. */
  imageAlt?: string;
  /** Photographer/wire-service credit shown alongside the image. */
  imageCredit?: string;
}

export interface ActionIssuesResponse {
  date: string | null;
  issues: ActionIssue[];
  availableDates?: string[];
  generatedAt?: string;
}
