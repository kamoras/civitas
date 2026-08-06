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

/** One statewide ballot measure. Every text field is verbatim from
 * `sourceName` — nothing here is model-generated, by design. See the
 * About page's "State Ballots & Ballot Measures" section for the
 * user-facing account, and AGENTS.md principle 7 for the rule. */
export interface BallotMeasure {
  id: string;
  state: string;
  /** ISO date of the election this measure appears on. Load-bearing:
   * a state can run the same measure number on a primary and a general
   * ballot, so a measure without its election date is ambiguous. */
  electionDate: string;
  electionType: string;
  number: string;
  title: string;
  measureType: string | null;
  origin: string | null;
  /** certified | removed | withdrawn | under_appeal. */
  status: string;
  officialTitle: string | null;
  officialSummary: string | null;
  fiscalImpact: string | null;
  /** The state's OWN framing of what a yes/no vote does, verbatim. Null
   * when the source publishes none — never inferred, because the
   * intuitive inference is inverted on a veto referendum (where
   * "approved" retains the law under challenge). */
  yesMeans: string | null;
  noMeans: string | null;
  /** Who drafted the title / fiscal note (legislature, attorney general,
   * legislative staff…). Rendered with the quote: ballot titles are
   * frequently litigated as slanted, so naming the author is more
   * neutral than the bare quote. */
  titleAuthority: string | null;
  fiscalAuthority: string | null;
  sourceName: string;
  sourceUrl: string | null;
  asOf: string | null;
}

/** Whether we actually know this state's measures — "the source says
 * none" and "we haven't ingested it" are different claims and must not
 * render alike. */
export interface MeasureCoverage {
  status: "covered" | "confirmed_none" | "not_yet_covered" | "ingest_failed";
  sourceName: string | null;
  checkedAt: string | null;
}

/** Where to go for the parts of the ballot this page cannot show. */
export interface OfficialLookup {
  url: string;
  label: string;
  sourceName: string;
  /** False = the generic national directory, because no verified
   * state-specific link exists. The page words the link differently for
   * each, rather than promising a state lookup it doesn't have. */
  isStateSpecific: boolean;
  verifiedAt: string | null;
}

export interface StateBallot {
  state: string;
  cycleYear: number;
  electionDate: string;
  electionType: string;
  statePvi: number | null;
  senateRaces: RaceSummary[];
  houseRaces: RaceSummary[];
  measures: BallotMeasure[];
  measureCoverage: MeasureCoverage;
  officialLookup: OfficialLookup;
  /** What this page deliberately does not cover, enumerated by the
   * backend so the limitation renders as content rather than a footnote. */
  omits: string[];
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

/** One curated town — see backend/app/data/town_directory.json. Not a
 * directory of all US municipalities: a small, hand-picked list. */
export interface TownEntry {
  name: string;
  /** The town's own government site (e.g. "City of Cambridge") — never a
   * visitor's address; see GOOGLE_CIVIC_API_KEY's comment in config.py. */
  sourceName: string;
}

export interface TownContest {
  kind: "contest";
  office: string;
  candidates: { name: string; party: string | null; candidateUrl: string | null }[];
}

/** A local measure from Google Civic — same verbatim-only contract as
 * BallotMeasure above, just a different upstream shape (no yes/no
 * framing or authority fields; Google's schema doesn't carry them). */
export interface TownMeasure {
  kind: "measure";
  title: string;
  subtitle: string | null;
  text: string | null;
  url: string | null;
  passageThreshold: string | null;
}

export type TownBallotItem = TownContest | TownMeasure;

export interface TownBallot {
  /** not_yet_covered: town isn't curated (or no source is configured).
   * ingest_failed: the live lookup failed. covered: succeeded — an empty
   * `contests` is real information ("nothing local at this address"),
   * not a failure. */
  status: "not_yet_covered" | "ingest_failed" | "covered";
  /** The representative address results were resolved against, when the
   * source is Google Civic — WE chose this, never a visitor's own
   * address. Null when the source is a town's own official ballot PDF
   * (no representative-address approximation needed) or status isn't
   * "covered". */
  address: string | null;
  /** Which source actually answered — a town's own official ballot PDF
   * when one is hand-verified to exist (see backend's
   * ballot_pdf_sources.json), otherwise Google Civic's representative-
   * address approximation. Null unless status is "covered". */
  source: string | null;
  /** Link to the real source document, when the source is a PDF. Null
   * for Google Civic (no single document to link) or when not covered. */
  sourceUrl: string | null;
  /** Which election these contests are actually FOR — load-bearing, not
   * decoration. Neither source is guaranteed to be answering for the
   * SAME election the rest of the page (federal races, statewide
   * measures) is titled for: a town's most recently published ballot
   * PDF may be an earlier primary, and Google Civic auto-selects from
   * whatever it has indexed, which is often not the upcoming general
   * either. Null unless status is "covered". */
  electionName: string | null;
  electionDate: string | null;
  contests: TownBallotItem[];
}
