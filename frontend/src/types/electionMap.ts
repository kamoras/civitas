/** GET /api/elections/map (backend app/forecast/build.py). Margins are the
 * Republican minus the Democratic share of the two-party vote, in points:
 * negative leans Democratic. No field is a chance of anyone winning. */

export type MapBin =
  "close" | "D_leans" | "D_usually" | "D_strongly" | "R_leans" | "R_usually" | "R_strongly";

export interface MapPoll {
  pollster: string;
  start: string;
  end: string;
  sample: number | null;
  population: string;
  moe: number | null;
  dem: number;
  rep: number;
  partisan: "" | "D" | "R";
  url: string | null;
  usedInAverage?: boolean;
  /** One poll the article prints asked two ways (with and without leaners,
   * say): every question's shares, the entry counted once (backend poll_list). */
  questions?: { dem: number; rep: number }[];
}

export interface PollSource {
  state: "ok" | "no_table" | "no_article" | "rejected" | "unreachable";
  detail?: string;
  checkedOn?: string;
  revisionDate?: string;
  /** The date of the last good revision whose polls are used instead. */
  keptFrom?: string;
}

export interface MapSeatArticle {
  title: string;
  revid?: number;
  timestamp?: string;
  url?: string;
  status?: string;
}

interface MapSeatBase {
  id: string;
  state: string;
  stateName: string;
  special: boolean;
  /** House seats: the district number (0 = at-large); null for the Senate. */
  district?: number | null;
  /** Null (status "no_lean") when Civitas has no usual-vote figure. */
  lean: number | null;
  leanLabel: string | null;
  usualBin: MapBin | null;
  dem: string | null;
  rep: string | null;
  incumbentParty: "D" | "R" | null;
  article?: MapSeatArticle;
}

export interface RangedSeat extends MapSeatBase {
  status: "range";
  prior: number;
  priorLabel: string;
  mean: number;
  meanLabel: string;
  range80: [number, number];
  /** "D+8 to R+2", written by the backend. */
  range80Label: string;
  /** The range, as written, reaches even: the backend's call, never re-derived. */
  close: boolean;
  pollSource: PollSource;
  range95: [number, number];
  pollsBin: MapBin;
  pollAverage: number | null;
  pollShare: number;
  polls: { count: number; used: number; latest: string | null; list: MapPoll[] };
  difference: { strength: 0 | 1 | 2; direction: "D" | "R" | null; seatPolls: boolean };
  /** Further out than the training polls reach: the range is the usual
   * vote, incumbency and the national swing alone; polls are listed beside it. */
  fundamentalsOnly?: boolean;
  /** What the range rests on, decided by the backend: the seat's own polls,
   * no poll of the seat (usual vote and national swing alone), or a date
   * further out than polls count. */
  rangeBasis: "polls" | "no_poll" | "before_window";
  /** "Margin range, from polls" and the like, written by the backend. */
  rangeBasisLabel: string;
  /** "from polls" / "no poll" / "polls not counted yet". */
  rangeBasisShort: string;
}

export interface UnrangedSeat extends MapSeatBase {
  status: "not_contested" | "not_two_party" | "unconfirmed" | "no_lean";
  reason: string;
  polls?: { count: number; list: MapPoll[] };
}

export type MapSeat = RangedSeat | UnrangedSeat;

export interface CalibrationBin {
  low: number;
  high: number;
  n: number;
  meanChance?: number;
  demShare?: number;
  demCarried?: number;
  years?: [number, number];
}

export type MapChamberKey = "senate" | "house";

export interface ChamberCalibration {
  cycles: [number, number];
  races: number;
  coverage80: number;
  coverage95: number;
  /** The structural line's own 80% ranges, held out (the map beyond the poll window). */
  coverage80Fundamentals?: number | null;
  meanAbsError: number;
  table: CalibrationBin[];
  seatTotals: {
    year: number;
    expectedDem: number;
    actualDem: number;
    races: number;
    independentSd: number;
  }[];
  seatTotalsOutside90: number;
  brier: { combined: number; fundamentals: number; pollsOnly: number; polledRaces: number };
  /** The newest cycle predicted by a fit on the cycles before it only. */
  forward?: {
    cycle: number;
    trainedOn: [number, number];
    races: number;
    polledRaces: number;
    coverage80: number;
    coverage95: number;
    coverage80Band: [number, number];
    meanAbsError: number;
    brier: { combined: number; fundamentals: number; pollsOnly: number };
    logLoss: number;
  } | null;
}

export interface ChamberModel {
  beta: [number, number, number, number];
  priorSd: number;
  halfLife: number;
  pollFloor: number;
  pollSpread: number;
  scale: number;
  priorScale?: number;
  file: string;
  fit: string;
  /** When the automatic refit last replaced the checked-in fit. */
  refreshed?: string | null;
}

export interface LeanSource {
  source: string;
  /** The presidential elections the lean is computed from, "2020+2024". */
  window: string;
  file: string;
  congress?: number;
}

export interface NationalInput {
  margin: number;
  label: string;
  /** The newest "updated" date of the averages read. */
  updated: string;
  /** "ok", or "kept" when the latest revision couldn't be read. */
  statusDetail?: string;
  averages: { source: string; updated: string; dem: number; rep: number; url: string | null }[];
  article: string;
  revid: number;
  url: string;
  status: string;
}

export interface TrainingPolls {
  attribution: string;
  url: string;
  terms: string | null;
}

export interface PollLicence {
  attribution: string;
  license: string;
  licenseUrl: string;
  notice: string;
}

/** The two units a seat's numbers come in, both written "D+N", each named
 * in one sentence by the backend. */
export interface MapUnits {
  lean: string;
  margin: string;
}

/** GET /api/elections/map as the backend sends it. */
export interface ElectionMapResponse {
  schemaVersion: number;
  asOf: string;
  electionDay: string;
  /** The election's year (the backend's active election), for every title. */
  cycle: number;
  daysToElection: number;
  horizon: number;
  /** True when the map is older than the backend's stale threshold. */
  stale?: boolean;
  /** True on an archived election's map: its last campaign build, kept
   * as it stood (GET /api/elections/archive/{cycle}/map). */
  frozen?: boolean;
  pollWindowDays: number;
  /** False further out than pollWindowDays: every range is fundamentals only. */
  pollsInRange?: boolean;
  national: NationalInput;
  bins: { leansMax: number; usuallyMax: number };
  units?: MapUnits;
  sources: {
    lean: Record<MapChamberKey, LeanSource>;
    training: Record<MapChamberKey, string>;
    trainingPolls?: TrainingPolls;
    polls: PollLicence;
  };
  chambers: Record<
    MapChamberKey,
    {
      shift: number;
      shiftLabel: string;
      calibration: ChamberCalibration;
      model: ChamberModel;
      seats: MapSeat[];
    }
  >;
  /** Both chambers' names; `chambers` holds only the one asked for (?chamber=). */
  chambersAvailable?: MapChamberKey[];
}

/** One chamber's map, as every component reads it (chamberView). */
export interface ElectionMapData {
  chamber: MapChamberKey;
  asOf: string;
  electionDay: string;
  cycle: number;
  daysToElection: number;
  horizon: number;
  national: NationalInput & { shift: number; shiftLabel: string };
  stale?: boolean;
  pollWindowDays: number;
  pollsInRange: boolean;
  bins: { leansMax: number; usuallyMax: number };
  units?: MapUnits;
  calibration: ChamberCalibration;
  model: ChamberModel;
  sources: {
    lean: LeanSource;
    training: string;
    trainingPolls?: TrainingPolls;
    polls: PollLicence;
  };
  seats: MapSeat[];
}
