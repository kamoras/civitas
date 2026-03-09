export interface SponsoredBill {
  billId: string;
  title: string;
  introducedDate: string;
  latestAction: string;
  latestActionDate: string;
  policyArea: string;
  policyAreas: PolicyAreaDetail[];
  partyLeaning: "R" | "D" | "bipartisan" | null;
  congress: number;
  billType: string;
  isLaw: boolean;
}

export interface Senator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  yearsInOffice: number;
  initials: string;
  representationScore: {
    fundingIndependence: number;
    promisePersistence: number;
    independentVoting: number;
    fundingDiversity: number;
    legislativeEffectiveness: number;
  };
  funding: {
    totalRaised: number;
    totalFromPACs: number;
    smallDonorPercentage: number;
    topDonors: Donor[];
    industryBreakdown: IndustryDonation[];
  };
  votingRecord: VotingRecord;
  lobbyingMatches: LobbyingMatch[];
  campaignPromises: CampaignPromise[];
  platformSummary: string;
  partisanDepth: PartisanDepth | null;
  sponsoredBills: SponsoredBill[];
  leadershipScore: number | null;
  ideologyScore: number | null;
  sponsorshipDescription: string;
}

export interface Donor {
  name: string;
  total: number;
  type: "PAC" | "Individual" | "SuperPAC" | "Org/Employees" | "Party/Ideological" | "CandidateAffiliated" | "Self-Funded";
  industry: string;
  pacSponsor: string | null;
  pacIndustry: string | null;
  pacAnalysis: string | null;
}

export interface IndustryDonation {
  industry: string;
  name: string;
  total: number;
  percentage: number;
}

export interface VotingRecord {
  totalVotes: number;
  votedWithPartyCount: number;
  votedAgainstPartyCount: number;
  partyLoyaltyPct: number;
  votingSummary: string;
  recentVoteCount: number;
  keyVoteCount: number;
}

export interface VoteCounts {
  all: number;
  yea: number;
  nay: number;
  againstParty: number;
}

export interface PaginatedVotes {
  votes: KeyVote[];
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
  category: string;
  filter: string;
  counts: VoteCounts;
}

export interface PolicyAreaDetail {
  area: string;
  confidence: number;
  party: "R" | "D" | "bipartisan";
}

export interface KeyVote {
  billName: string;
  billId: string;
  date: string;
  vote: "Yea" | "Nay" | "Not Voting";
  policyArea: string;
  policyAreas: PolicyAreaDetail[];
  partyAlignmentWeight: number;
  stance: string;
  description: string;
  partyLeaning: "R" | "D" | "bipartisan" | null;
  votedWithParty: boolean | null;
  voteCategory: "recent" | "key";
  keyVoteReasoning: string | null;
}

export interface LobbyingMatch {
  lobbyistOrg: string;
  industry: string;
  lobbyingSpend: number;
  donationToSenator: number;
  billsInfluenced: string[];
  senatorVoteAligned: boolean | null;
  description: string;
}

export interface PolicyAlignment {
  area: string;
  alignment: "R" | "D" | "bipartisan";
  strength: number;
}

export interface PartisanDepth {
  overallLean: number;
  overallParty: "R" | "D" | "centrist";
  depth: "deep" | "moderate" | "centrist" | "cross-cutting";
  crossPartyCount: number;
  totalPositions: number;
  policyBreakdown: PolicyAlignment[];
}

export interface CampaignPromise {
  promiseText: string;
  category: string;
  alignment: "kept" | "broken" | "partial" | "unclear";
  relatedVotes: string[];
  analysis: string;
  partyAlignment: "R" | "D" | "bipartisan" | null;
}

export interface ScoreTrend {
  direction: "up" | "down" | "stable" | "new";
  change: number;
  previousScore: number | null;
}

export interface LeaderboardEntry {
  id: string;
  name: string;
  state: string;
  district?: number;
  party: "D" | "R" | "I";
  yearsInOffice: number;
  initials: string;
  representationScore: Senator["representationScore"];
  totalRaised: number;
  totalFromPacs: number;
  smallDonorPercentage: number;
  topIndustry: string | null;
  trend?: ScoreTrend;
}
