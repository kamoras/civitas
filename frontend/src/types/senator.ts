export type IndustryCode =
  | "PHARMA"
  | "INSURANCE"
  | "OIL_GAS"
  | "DEFENSE"
  | "FINANCE"
  | "REAL_ESTATE"
  | "TECH"
  | "TELECOM"
  | "AGRIBUSINESS"
  | "ENERGY"
  | "CONSTRUCTION"
  | "TRANSPORT"
  | "LAWYERS"
  | "LOBBYISTS"
  | "GAMBLING"
  | "GUNS"
  | "TOBACCO"
  | "CRYPTO"
  | "PRIVATE_PRISON"
  | "POLITICAL"
  | "OTHER"
  | "SMALL_DONORS"
  | "LARGE_INDIVIDUAL";

export interface Senator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  yearsInOffice: number;
  initials: string;
  representationScore: {
    constituentFunding: number;
    independenceIndex: number;
    donorDiversity: number;
    promiseFulfillment: number;
    accountability: number;
  };
  funding: {
    totalRaised: number;
    totalFromPACs: number;
    smallDonorPercentage: number;
    topDonors: Donor[];
    industryBreakdown: IndustryDonation[];
  };
  votingRecord: {
    totalVotes: number;
    proCorporateVotes: number;
    proConsumerVotes: number;
    votedWithPartyCount: number;
    votedAgainstPartyCount: number;
    partyLoyaltyPct: number;
    votingSummary: string;
    recentVotes: KeyVote[];
    keyVotes: KeyVote[];
  };
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
  industry: IndustryCode;
  name: string;
  total: number;
  percentage: number;
}

export interface KeyVote {
  billName: string;
  billId: string;
  date: string;
  vote: "Yea" | "Nay" | "Not Voting";
  // New policy stance fields
  policyArea: string;
  stance: string;
  stanceVote: "Yea" | "Nay" | null;
  impactedGroups: string[];
  // Legacy fields (kept for transition)
  proBusinessVote: "Yea" | "Nay" | null;
  classification: string;
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
  industry: IndustryCode;
  lobbyingSpend: number;
  donationToSenator: number;
  billsInfluenced: string[];
  senatorVoteAligned: boolean;
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
  representationScore: Senator["representationScore"];
  totalRaised: number;
  totalFromPacs: number;
  smallDonorPercentage: number;
  topIndustry: string | null;
}
