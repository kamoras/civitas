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
  | "OTHER";

export interface Senator {
  id: string;
  name: string;
  state: string;
  party: "D" | "R" | "I";
  yearsInOffice: number;
  punkNickname: string;
  initials: string;
  corruptionScore: {
    corporateFunding: number;
    lobbyistAlignment: number;
    industryConcentration: number;
    flipFlopIndex: number;
    revolvingDoor: number;
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
    keyVotes: KeyVote[];
  };
  lobbyingMatches: LobbyingMatch[];
}

export interface Donor {
  name: string;
  total: number;
  type: "PAC" | "Individual" | "SuperPAC" | "Org/Employees" | "Party/Ideological";
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
  proBusinessVote: "Yea" | "Nay" | null;
  classification: "pro-corporate" | "pro-consumer" | "mixed";
  description: string;
  corporateInterest: string;
  publicImpact: string;
  relevantDonors: string[];
  relevantDonorTotal: number;
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
