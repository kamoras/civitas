export interface Senator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  yearsInOffice: number;
  initials: string;
  approvalRating: number | null;
  disapprovalRating: number | null;
  representationScore: {
    fundingIndependence: number;
    promisePersistence: number;
    independentVoting: number;
    fundingDiversity: number;
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
}

export interface Donor {
  name: string;
  total: number;
  type: "PAC" | "Individual" | "SuperPAC" | "Org/Employees" | "Party/Ideological" | "CandidateAffiliated";
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

export interface PolicyBreakdown {
  policyArea: string;
  totalVotes: number;
  withStance: number;
  againstStance: number;
}

export interface VotingRecord {
  totalVotes: number;
  scoreableVotes: number;
  donorAlignedVotes: number;
  donorOpposedVotes: number;
  policyBreakdown: PolicyBreakdown[];
  votedWithPartyCount: number;
  votedAgainstPartyCount: number;
  partyLoyaltyPct: number;
  votingSummary: string;
  recentVotes: KeyVote[];
  keyVotes: KeyVote[];
}

export interface KeyVote {
  billName: string;
  billId: string;
  date: string;
  vote: "Yea" | "Nay" | "Not Voting";
  policyArea: string;
  stance: string;
  stanceVote: "Yea" | "Nay" | null;
  impactedGroups: string[];
  affectedIndustries: string[];
  description: string;
  corporateInterest: string;
  publicImpact: string;
  relevantDonors: string[];
  relevantDonorTotal: number;
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

export interface CampaignPromise {
  promiseText: string;
  category: string;
  alignment: "kept" | "broken" | "partial" | "unclear";
  relatedVotes: string[];
  analysis: string;
}

export interface LeaderboardEntry {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  yearsInOffice: number;
  initials: string;
  approvalRating: number | null;
  disapprovalRating: number | null;
  representationScore: Senator["representationScore"];
  totalRaised: number;
  totalFromPacs: number;
  smallDonorPercentage: number;
  topIndustry: string | null;
}
