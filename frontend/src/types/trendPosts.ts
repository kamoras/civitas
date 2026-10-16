/** GET /api/admin/trend-posts (backend analyze/trend_poster.py): the trend
 *  posts' audit. Every null is "not measured", never zero. */

export interface PartyCounts {
  D: number;
  R: number;
  none: number;
}

export interface TrendPartyAudit {
  pool: PartyCounts;
  posted: PartyCounts;
  poolDemocraticShare: number | null;
  postedDemocraticShare: number | null;
  pValue: number | null;
  minPosted: number;
  alpha: number;
  diverges: boolean;
}

export interface TrendLifetimes {
  topics: number;
  ended: number;
  p25: number | null;
  median: number | null;
  p75: number | null;
}

export interface TrendEmbedTrialRow {
  embedKind: string;
  posts: number;
  read: number;
  meanLikes: number | null;
  medianLikes: number | null;
  meanReposts: number | null;
}

export interface TrendCandidate {
  topic: string;
  name: string;
  rank: number | null;
  firstSeen: string;
  decidedAt: string;
  relevance: number | null;
  tragedy: number | null;
  loaded: number | null;
  neutrality: string[];
  matchType: string | null;
  matchStrength: number | null;
  recordKind: string | null;
  recordKey: string | null;
  recordDate: string | null;
  recordUrl: string | null;
  namedParties: string[];
  sponsorParties: string[];
  selectionScore: number | null;
  decision: string;
  reason: string | null;
}

export interface TrendPostsAudit {
  partyAudit: TrendPartyAudit;
  partyAuditAllTime: TrendPartyAudit;
  lifetimes: TrendLifetimes;
  embedTrial: TrendEmbedTrialRow[];
  decisions: Record<string, number>;
  candidates: TrendCandidate[];
}
