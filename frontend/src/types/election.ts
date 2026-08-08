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
  /** FEC candidate-status code: "C"=statutory candidate, "F"=future cycle,
   * "N"=not yet statutory, "P"=prior cycle. Null if FEC hasn't set one. */
  candidateStatus: string | null;
  hasRaisedFunds: boolean;
  /** Fundraising figures — null until this candidate's turn comes up in
   * the backend's prioritized/watermarked financial-refresh queue (FEC's
   * API is rate-limited, so not every candidate is refreshed every run).
   * Never a fabricated 0. */
  contributions: number | null;
  cashOnHand: number | null;
  /** ISO 8601 (UTC, explicit Z/offset) timestamp of the last successful
   * FEC financials sync for this candidate — null means the figures above
   * have never been synced, i.e. "no data yet", not "raised $0". */
  lastFinancialsSync: string | null;
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
  /** Which map `pvi` came from: "district" (House with district data),
   * "state" (statewide number — a fallback when used for a House race),
   * or null when `pvi` is null. */
  pviLevel: "district" | "state" | null;
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
  /** See RaceSummary.pviLevel. */
  pviLevel: "district" | "state" | null;
  candidates: CandidateSummary[];
  coverage: RaceCoverageItem[];
}

/** A candidate's matching Senator/Representative scorecard row — only
 * ever present for a real, uniquely-identified match (see backend's
 * elections.py _incumbent_link); never a guess. */
export interface IncumbentRecord {
  id: string;
  /** Weighted overall Representation Score, 0-100 (score_calculator.
   * compute_overall_score — the same formula the leaderboard and
   * profile page use, not a separately-derived number). */
  score: number;
}

/** CandidateSummary plus incumbentRecord — only the ballot endpoint
 * (GET /elections/states/{state}) populates this; other endpoints'
 * candidates don't carry it. */
export interface BallotCandidate extends CandidateSummary {
  /** Null unless this candidate is a sitting Senator/Representative AND
   * a real, unambiguous match was found — never populated as a guess. */
  incumbentRecord: IncumbentRecord | null;
}

/** One federal race with EVERY candidate — RaceDetail minus the
 * coverage feed, which stays one click away on the race-detail page.
 * Backs the ballot-centric per-state view (StateBallot below), which
 * must show every real option, not RaceSummary's top-2-by-funds. */
export interface RaceWithCandidates {
  id: string;
  cycleYear: number;
  office: string;
  state: string;
  district: number | null;
  isSpecial: boolean;
  pvi: number | null;
  /** See RaceSummary.pviLevel. */
  pviLevel: "district" | "state" | null;
  candidates: BallotCandidate[];
}

/** Every federal race on one state's ballot this cycle — GET
 * /elections/states/{state}. Deliberately federal-races-only: no
 * statewide ballot measures or local races here (a separate feature). */
export interface StateBallot {
  state: string;
  cycleYear: number;
  electionDate: string;
  /** Statewide PVI — null only if the underlying PVI map lacks this
   * state, never a fabricated 0. */
  statePvi: number | null;
  senateRaces: RaceWithCandidates[];
  houseRaces: RaceWithCandidates[];
}

/** Provenance block on the /pvi response. Optional end to end — older
 * backend responses (and cached ones) may omit it entirely. */
export interface PviMeta {
  states?: { source: string; method: string; window: string; asOf: string };
  districts?: { source: string; window: string; asOf: string };
  /** e.g. "Cook-PVI-style partisan lean relative to the national
   * presidential vote. Measures lean, not a race forecast." */
  note?: string;
}

export interface PviMap {
  /** "ST" -> signed int PVI, e.g. { "GA": 3 }. */
  states: Record<string, number>;
  /** "ST-N" -> signed int PVI, e.g. { "CA-12": -13 }. */
  districts: Record<string, number>;
  /** Methodology/provenance — treat as possibly missing. */
  meta?: PviMeta;
}
