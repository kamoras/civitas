export interface PresidentialScore {
  /** Any dimension may be null — genuinely inapplicable for this
   * president (e.g. Public Mandate for one who never won a presidential
   * election), never a fabricated or neutral placeholder. Competence
   * (executive-order activity rate) was removed entirely (2026-07) — its
   * only live component measured no relationship (Spearman 0.097) with
   * real administrative-skill judgment. See PRESIDENT_SCORE_WEIGHTS's
   * comment in the backend's config_definitions.py for the full account. */
  publicMandate: number | null;
  effectiveness: number | null;
  agencyAlignment: number | null;
  /** C-SPAN Presidential Historians Survey, z-scored. Null for any
   * currently-serving or just-departed president — the survey only rates
   * a completed term, and its 2025 cycle was postponed entirely. */
  historicalLegacy: number | null;
  /** Backend-computed weighted total, renormalized over whichever
   * dimensions are non-null — never recompute this client-side. */
  overall: number;
  /** How many of the 4 possible dimensions actually have a score (0-4).
   * A composite built from fewer signals (a short-tenure or currently-
   * serving president) shouldn't be read with the same confidence as one
   * built from all 4 — surfaced so that's never implied silently. */
  dimensionsAvailable: number;
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
  /** Informational only, not a scoring input (2026-07) — Competence, the
   * dimension this used to feed, was removed entirely. */
  eoCount: number | null;
  /** Pre-polling-era (pre-Truman) Public Mandate proxy: average margin of
   * victory across this president's own election win(s). Null for
   * presidents with live approval data, and for the five who never won a
   * presidential election in their own right. */
  electionMargin: number | null;
  /** Raw C-SPAN 2021 Presidential Historians Survey point total. */
  historicalLegacyScore: number | null;
  /** Average approval over a rolling last-90-days window rather than the
   * full term — informational, not part of any scored dimension. Null
   * once a president leaves office and no new polls populate it. */
  recentAvgApproval: number | null;
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
