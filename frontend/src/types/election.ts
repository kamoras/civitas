/** Midterm-elections feature types (2026-07). Mirrors the backend's
 * /api/elections/* response shapes exactly — see backend/app/api/elections.py.
 */

export interface CandidateSummary {
  id: string;
  name: string;
  party: string;
  /** FEC code: "I"=Incumbent, "C"=Challenger, "O"=Open seat. Null if FEC
   * hasn't classified this candidate yet. */
  incumbentChallenge: string | null;
  hasRaisedFunds: boolean;
  /** Fundraising figures — null until this candidate's turn comes up in
   * the backend's prioritized/watermarked financial-refresh queue (FEC's
   * API is rate-limited, so not every candidate is refreshed every run).
   * Never a fabricated 0. */
  contributions: number | null;
  cashOnHand: number | null;
}

export interface RaceSummary {
  id: string;
  cycleYear: number;
  /** "S" = Senate, "H" = House — reuses FEC's own office codes. */
  office: string;
  state: string;
  district: number | null;
  isSpecial: boolean;
  /** Cook-PVI-equivalent, positive = R lean, negative = D lean. Null only
   * if the underlying PVI data file is unavailable (see backend's
   * score_calculator.get_state_pvi_map/get_district_pvi_map) — never a
   * fabricated 0 standing in for "no lean". */
  pvi: number | null;
  candidateCount: number;
  /** Top 2 candidates by cash on hand — for the map/directory summary view. */
  topCandidates: CandidateSummary[];
}

export interface RaceCoverageItem {
  id: number;
  sourceType: "news" | "bluesky";
  sourceName: string;
  title: string;
  url: string;
  /** Verbatim from the source (RSS description or Bluesky post text) —
   * never LLM-generated, so this carries no hallucination risk. */
  summary: string;
  author: string | null;
  publishedAt: string | null;
}

export interface RaceDetail {
  id: string;
  cycleYear: number;
  office: string;
  state: string;
  district: number | null;
  isSpecial: boolean;
  pvi: number | null;
  candidates: CandidateSummary[];
  coverage: RaceCoverageItem[];
}

export interface CandidateDetail extends CandidateSummary {
  disbursements: number | null;
  individualItemizedContributions: number | null;
  lastFinancialsSync: string | null;
  race: {
    id: string;
    office: string;
    state: string;
    district: number | null;
  } | null;
}

export interface PviMap {
  /** "ST" -> signed int PVI, e.g. { "GA": 3 }. */
  states: Record<string, number>;
  /** "ST-N" -> signed int PVI, e.g. { "CA-12": -13 }. */
  districts: Record<string, number>;
}
