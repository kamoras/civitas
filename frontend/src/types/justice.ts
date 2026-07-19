export interface JusticeScore {
  consistency: number;
  independence: number;
  bipartisanAgreement: number;
  judicialRestraint: number;
  /** Backend-computed weighted total — never recompute this client-side. */
  overall: number;
}

export interface Justice {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  dateStart: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  unanimousPct: number;
  authoredMajority: number;
  authoredDissent: number;
  authoredConcurrence: number;
  closeCaseMajorityPct: number;
  crossBlocPct: number;
  agreementMatrix: Record<string, number>;
  summary: string;
}

export interface JusticeLeaderboardEntry {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  isActive: boolean;
  thumbnailUrl: string | null;
  score: JusticeScore;
  casesDecided: number;
  majorityPct: number;
  dissentPct: number;
  crossBlocPct: number;
}
