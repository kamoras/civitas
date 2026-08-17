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
  /** Which race this item is about — only populated on the state
   * ballot's aggregated coverage feed (GET /elections/states/{state}),
   * which spans every race in the state; a single-race feed like
   * RaceDetail's doesn't need it since the page context already says
   * which race. */
  race?: { id: string; office: string; district: number | null };
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
 * coverage feed, which is aggregated separately across the whole state
 * (StateBallot.coverage below) rather than repeated per-race. Backs the
 * ballot-centric per-state view (StateBallot below), which must show
 * every real option, not RaceSummary's top-2-by-funds. */
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
  /** WHICH answer this race's candidate list is, decided by the backend
   * (never re-derived here): "confirmed" = the state has named its whole
   * November ballot, minor parties included; "nominees" = the state
   * confirmed nominees from PRIMARY results, which cannot see a
   * Libertarian, Green or independent who never ran in a primary, so the
   * list is real but incomplete; "primary" = no nominee yet, but the
   * state lists these as on its primary ballot; "filers" = nobody has
   * confirmed anything, so this is every active FEC filer, some of whom
   * may never appear on a ballot. Four quite different things, and the
   * page says which one a reader is looking at. */
  candidateSource: "confirmed" | "nominees" | "primary" | "filers";
  /** House races only (null for Senate): this district's counties, from
   * the Census Bureau's block-to-district assignment. A "(part)" suffix
   * means that county also has population in another district. Lets a
   * voter who knows their county but not their district number pick it
   * out. Null (not []) if this district isn't in the bundled crosswalk —
   * never a guess. */
  counties: string[] | null;
  candidates: BallotCandidate[];
}

/** Every federal race on one state's ballot this cycle — GET
 * /elections/states/{state}. Deliberately federal-races-only: no
 * statewide ballot measures or local races here (a separate feature). */
export interface StateBallot {
  state: string;
  cycleYear: number;
  electionDate: string;
  /** This state's own primary date, read from the state's election feed.
   * Null when that state publishes nothing this can be read from — an
   * unknown date is shown as unknown, never guessed. */
  primaryDate: string | null;
  /** Statewide PVI — null only if the underlying PVI map lacks this
   * state, never a fabricated 0. */
  statePvi: number | null;
  senateRaces: RaceWithCandidates[];
  houseRaces: RaceWithCandidates[];
  /** Every race's news/Bluesky coverage in this state, newest first,
   * deduplicated by url and capped to a teaser count (see backend's
   * _state_coverage) — shown front-and-center at the top of the ballot
   * page rather than requiring a click into a specific race. */
  coverage: RaceCoverageItem[];
}

/** GET /elections/geocode response — resolves a mailing address to a
 * state + House district via the Census Bureau's free geocoder, so the
 * ballot page can auto-select a visitor's district instead of requiring
 * the manual dropdown. Both null (never a guess) when Census can't match
 * the address or it doesn't resolve to a congressional district. The
 * address itself is never returned, logged, or stored server-side. */
export interface GeocodeResult {
  state: string | null;
  district: number | null;
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
  /** current_election_cycle() — same source of truth as every race's
   * own cycleYear, included here so the /elections directory page can
   * label its header from this one fetch. */
  cycleYear?: number;
}
