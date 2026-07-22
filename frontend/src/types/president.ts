export interface PresidentialScore {
  /** Any dimension may be null — genuinely inapplicable for this
   * president (e.g. Public Mandate for one who never won a presidential
   * election), never a fabricated or neutral placeholder. */
  publicMandate: number | null;
  effectiveness: number | null;
  competence: number | null;
  agencyAlignment: number | null;
  /** Backend-computed weighted total, renormalized over whichever
   * dimensions are non-null — never recompute this client-side. */
  overall: number;
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
  /** Pre-polling-era (pre-Truman) Public Mandate proxy: average margin of
   * victory across this president's own election win(s). Null for
   * presidents with live approval data, and for the five who never won a
   * presidential election in their own right. */
  electionMargin: number | null;
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
