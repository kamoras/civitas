export interface President {
  id: string;
  name: string;
  party: string;
  number: number;
  termStart: string;
  termEnd: string | null;
  isCurrent: boolean;
  /** The API still sends `score` (0-100, deprecated); the site shows the
   *  record it was computed from instead, so it is not typed here. */
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
