export interface PresidentialScore {
  independence: number;
  followThrough: number;
  publicMandate: number;
  effectiveness: number;
  competence: number;
  agencyAlignment: number;
}

export interface President {
  id: string;
  name: string;
  party: string;
  number: number;
  termStart: string;
  termEnd: string | null;
  isCurrent: boolean;
  score: PresidentialScore;
  avgApproval: number | null;
  gdpGrowthAvg: number | null;
  jobsCreatedMillions: number | null;
  eoCount: number | null;
  eoCourtSuccessPct: number | null;
  cabinetTurnoverPct: number | null;
  /** True if Competence blended in live EO-activity data for this term;
   * false means it's entirely an editorial estimate (see /about). */
  competenceHasLiveData: boolean;
  summary: string;
  keyAchievements: string[];
  keyFailures: string[];
}

export interface PresidentLeaderboardEntry {
  id: string;
  name: string;
  party: string;
  number: number;
  termStart: string;
  termEnd: string | null;
  isCurrent: boolean;
  score: PresidentialScore;
  avgApproval: number | null;
  gdpGrowthAvg: number | null;
}
