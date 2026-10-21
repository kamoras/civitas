/** GET /api/court and its pages (backend services/court_service.py). Every
 *  figure is counted by the backend from the stored votes and Martin-Quinn
 *  positions: records and measurements, never a score or a rank. */

/** A justice's Martin-Quinn estimate for one term: the posterior mean and,
 *  where the file gave it, the 95% credible interval. Negative is liberal,
 *  positive conservative. */
export interface CourtPosition {
  term: number;
  position: number;
  low: number | null;
  high: number | null;
}

export interface CourtJustice {
  id: string;
  name: string;
  lastName: string;
  roleTitle: string;
  appointingPresident: string | null;
  appointingParty: string | null;
  dateStart: string | null;
  photo: string | null;
  /** Null for a justice with no position for `mqTerm` (newly confirmed). */
  mq: CourtPosition | null;
  /** The place on the line ("Furthest left of 9"); null with no position. */
  rank: string | null;
}

/** One pair of sitting justices: the cases both decided and how many of
 *  them they decided the same way; `share` is the percentage. */
export interface CourtPair {
  a: string;
  b: string;
  agree: number;
  both: number;
  share: number;
}

export interface CaseLinks {
  oyez: string | null;
  /** The Court's slip opinion, once posted. */
  opinion: string | null;
  /** The opinion's page in Explore. */
  explore: string | null;
}

export interface CourtDecision {
  caseId: string;
  name: string;
  term: string;
  decided: string | null;
  majorityVotes: number;
  minorityVotes: number;
  /** "6-3": majority votes, then minority. */
  split: string;
  unanimous: boolean;
  majority: string[];
  minority: string[];
  /** Who wrote the opinion of the Court; null for a per curiam. */
  author: string | null;
  links: CaseLinks;
}

export interface CourtDecisionPage {
  total: number;
  page: number;
  perPage: number;
  decisions: CourtDecision[];
}

/** The Court-level finding on votes under the appointing president
 *  (GET /api/court/finding, app/data/court_finding.json from
 *  scripts/research_justice_loyalty.py): shares, 0.025 is 2.5 points. */
export interface AppointerFinding {
  estimate: number;
  low: number;
  high: number;
  firstTerm: number;
  lastTerm: number;
  justices: number;
  draws: number;
  /** Whether the 95% interval leaves out no effect, decided by the backend. */
  excludesZero: boolean;
  scdbRelease: string;
  source: string | null;
}

export interface Court {
  /** Sitting justices, left to right by `mq`; those without one last. */
  justices: CourtJustice[];
  mqTerm: number | null;
  /** The median justice by position: one id, two for an even bench placed,
   *  none when no position is stored. */
  median: string[];
  record: {
    cases: number;
    unanimous: number;
    oneVote: number;
    unanimousPct: number | null;
    oneVotePct: number | null;
    /** Terms with decided cases stored, oldest first. */
    terms: number[];
  };
  /** The term in session by the calendar, and how many of its cases are
   *  decided and stored (0 early in a term). */
  currentTerm: { term: number; decided: number };
  agreement: CourtPair[];
  lineups: {
    /** Every split seen, lopsided first ("9-0" … "5-4"). */
    splits: string[];
    terms: { term: number; cases: number; counts: Record<string, number> }[];
  };
  decisions: CourtDecisionPage;
  appointerFinding: AppointerFinding | null;
}

/** One case on a justice's page. */
export interface JusticeCase {
  caseId: string;
  name: string;
  term: string;
  decided: string | null;
  vote: "majority" | "minority" | string;
  /** The opinion the justice wrote: "majority", "dissent", "concurrence"
   *  or "none". */
  opinion: string;
  split: string;
  links: CaseLinks;
}

export interface JusticeCasePage {
  total: number;
  page: number;
  perPage: number;
  terms: number[];
  cases: JusticeCase[];
}
