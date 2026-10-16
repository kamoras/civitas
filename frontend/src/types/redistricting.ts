/** GET /api/elections/redistricting[/{state}] — what changed on the new
 * U.S. House maps. Every figure is computed by the backend
 * (app/redistricting.py); the page only words it. PVI: positive = R lean,
 * negative = D lean. */

export type LeanCategory = "R" | "EVEN" | "D";

export interface DistrictShare {
  district: string;
  number: number;
  residents: number;
  /** Whole percent; never 100 unless all, never 0 unless none. */
  sharePct: number;
}

export interface NewDistrict {
  district: string;
  number: number;
  population: number;
  /** Old districts its residents came from, largest first (overlaps of
   * at least minResidents). */
  sources: DistrictShare[];
  /** Residents from overlaps under minResidents (each within about one
   * Census block of the line). */
  sliverResidents: number;
  predecessor: string;
  predecessorNumber: number;
  predecessorSharePct: number;
  /** The predecessor's lean on the old lines. */
  pviBefore: number | null;
  /** The residents' old districts' leans, population-weighted. */
  pviBeforeWeighted: number | null;
  pviAfter: number | null;
  pviShift: number | null;
  /** Civitas's computed lean of the predecessor (old lines) and of this
   * district, beside Cook's; never scored. */
  computedLeanBefore?: number | null;
  computedLeanAfter?: number | null;
  leanBefore: LeanCategory | null;
  leanAfter: LeanCategory | null;
  unchanged: boolean;
  counties: string[];
  places: string[];
}

export interface OldDistrict {
  district: string;
  number: number;
  population: number;
  destinations: DistrictShare[];
  sliverResidents: number;
  pvi: number | null;
  /** Civitas's computed lean on the old lines, beside Cook's. */
  computedLean?: number | null;
  hasSuccessor: boolean;
  counties: string[];
  places: string[];
}

export interface MapSource {
  reason: string | null;
  title: string | null;
  issuedBy: string | null;
  url: string | null;
}

export interface RedistrictingSummary {
  seats: number;
  population: number;
  unchangedSeats: number;
  oldWithoutSuccessor: number;
  residentsMoved: number;
  residentsMovedPct: number;
  /** Districts by lean category on each map, and the net change
   * (after − before): the headline "how many seats changed". */
  seatsByLeanBefore: Record<LeanCategory, number>;
  seatsByLeanAfter: Record<LeanCategory, number>;
  netSeatChangeByLean: Record<LeanCategory, number>;
  /** Expert layer: new districts whose category differs from their main
   * predecessor's. Not the net change; see the method section. */
  districtsLeaningDifferentlyFromPredecessor: number;
  meanPviBefore: number | null;
  meanPviAfter: number | null;
  meanResidentShift: number | null;
}

export interface PviProvenance {
  source: string | null;
  lines: string | null;
  window: string | null;
  congress: number;
  revision: { page: string; revid: number; timestamp: string } | null;
}

/** Where Civitas's computed lean comes from (app/data/district_lean.json). */
export interface ComputedLeanProvenance {
  source: string | null;
  method: string | null;
  window: string | null;
  asOf: string | null;
  href: string;
}

export interface StateRedistricting {
  state: string;
  stateName: string;
  cycle: string;
  fromCongress: number;
  toCongress: number;
  minResidents: number;
  map: MapSource | null;
  summary: RedistrictingSummary;
  newDistricts: NewDistrict[];
  oldDistricts: OldDistrict[];
  /** [old district, new district, residents], every overlap, unfloored. */
  cells: [string, string, number][];
  sources: {
    overlap: string | null;
    pviBefore: PviProvenance | null;
    pviAfter: PviProvenance | null;
    computedLean?: ComputedLeanProvenance | null;
  };
  /** The members now sitting were elected on the old lines. */
  fromCongressSitting: boolean;
}

export interface RedistrictingIndex {
  cycle: string | null;
  /** Whether the cycle's election is still ahead (false once it is held). */
  upcoming?: boolean;
  fromCongress: number | null;
  toCongress: number | null;
  states: {
    state: string;
    stateName: string;
    map: MapSource | null;
    summary: RedistrictingSummary;
  }[];
  /** States whose new map is not in force this cycle, with why. */
  heldMaps: { state: string; stateName: string; reason: string | null; url: string | null }[];
}

/** The ballot page's per-race figures (StateBallot house races). */
export interface RedistrictingNote {
  predecessorNumber: number;
  predecessorSharePct: number;
  sourceCount: number;
  pviBefore: number | null;
  pviAfter: number | null;
  unchanged: boolean;
}

/** GET /api/elections/redistricting/{state}/old/{n}. */
export interface RedistrictedSeat {
  seat: OldDistrict | null;
  state: string;
  stateName: string;
  cycle: string | null;
  fromCongressSitting: boolean;
}
