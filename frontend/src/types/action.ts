export interface RelatedExploreDoc {
  id: number;
  title: string;
  docType: string;
  date: string;
  url: string | null;
  commentUrl?: string | null;
  commentsCloseOn?: string | null;
  /** The issuing agency, for a Federal Register document. */
  agencyName?: string | null;
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
  officePhone?: string | null;
}

export interface CommitteeLeader {
  id: string;
  name: string;
  party: string;
  state: string;
  chamber: "senate" | "house";
}

/** A contact_committee action's committee (backend app/committee_routing.py),
 *  with its chair and ranking member as the stored rosters name them now. */
export interface CommitteeContact {
  code: string;
  name: string;
  chamber: "senate" | "house";
  url?: string | null;
  chair?: CommitteeLeader | null;
  rankingMember?: CommitteeLeader | null;
}

export interface ActionItem {
  text: string;
  type: string;
  url?: string | null;
  /** comment_period: the Explore document and its closing date (served only while open). */
  docId?: number | null;
  closesOn?: string | null;
  /** contact_committee */
  committee?: CommitteeContact | null;
}

export interface RelatedBill {
  name: string;
  id: string;
  url: string;
  /** Path to our own bill page ("/congress/bills/HR.22") when we host this bill. */
  internalUrl?: string | null;
  /** Where the bill stands, from the stored record (null when none is held). */
  stageName?: string | null;
  latestAction?: string | null;
  latestActionDate?: string | null;
}

/** Who can act on an issue next: from a linked bill's stage or an open
 *  comment period's agency (backend api/action.py), never guessed. */
export interface Decider {
  body: string;
  basis: string;
}

/** A roll call linked to an issue (the vote it reports, or a vote on a bill
 *  its coverage names). */
export interface IssueVote {
  chamber: "senate" | "house";
  congress: number;
  session: number;
  number: number;
  date: string;
  question: string;
  result: string;
  billId?: string | null;
  billLabel?: string | null;
}

export interface IssueMember {
  id: string;
  name: string;
  party: string;
  chamber: "senate" | "house";
  district?: number | null;
  officePhone: string;
  contactFormUrl: string;
  websiteUrl: string;
  /** {"house-119-2-314": "Yea"}: how the member voted on each linked roll call. */
  positions: Record<string, string>;
}

/** GET /api/action/issues/{id}/members?state= */
export interface IssueMembers {
  state: string;
  votes: IssueVote[];
  members: IssueMember[];
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
  /** The article each fact was quoted from, aligned with `facts`. */
  factSourceUrls?: string[];
  /** The summary's outlet and article: it is a quoted line too. */
  summarySource?: string | null;
  summarySourceUrl?: string | null;
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
  /** The monitors tracking this issue that still exist, by stored title. */
  relatedMonitors?: { slug: string; title: string }[];
  deciders?: Decider[];
  /** How many roll calls are linked (each member's record on them is in IssueMembers). */
  voteCount?: number;
  fullStory?: string | null;
  /** Only ever true from the issues-list fetch — the single-issue lookup
   *  has no peer issues to judge traction against. */
  isTrending: boolean;
  /** "developing" is a primary-source-only draft awaiting press
   *  corroboration (see backend early_signal.py) — shown with a
   *  disclosure badge and ranked after every "confirmed" issue. */
  status: "developing" | "confirmed";
  /** False once the issue is off the Action Center. A "developing" issue
   *  that is not current was retired (retiredReason says why), and never
   *  reads "Developing". Optional for an older backend. */
  isCurrent?: boolean;
  /** Why a retired draft left: "unconfirmed" (no coverage in time) reads
   *  "Not confirmed"; "covered" (a reported issue covers it) reads
   *  "Superseded". Null while current. */
  retiredReason?: string | null;
  /** The issue this one duplicates — for a retired vote draft, the reported
   *  issue covering the same vote. */
  coveredBy?: string | null;
  /** What a developing issue was drafted from — "senate_roll_call_vote",
   *  "house_roll_call_vote", "federal_register_significant_rule",
   *  "election_results" — null for news-derived issues. Optional for an
   *  older backend mid-rollout. */
  sourceType?: string | null;
  /** A seat-flip issue's count: when its figures were read (UTC ISO) and
   * whether the state lists it as official. Null otherwise; optional for an
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
  /** The newest run that published or re-matched an issue. */
  generatedAt?: string;
  /** The newest run that read the news, whether or not it changed anything. */
  checkedAt?: string;
}
