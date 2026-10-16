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
}

export interface ChamberModel {
  beta: [number, number, number, number];
  priorSd: number;
  halfLife: number;
  pollFloor: number;
  pollSpread: number;
  scale: number;
  file: string;
  fit: string;
}

export interface LeanSource {
  source: string;
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

export interface PollLicence {
  attribution: string;
  license: string;
  licenseUrl: string;
  notice: string;
}

/** GET /api/elections/map as the backend sends it. */
export interface ElectionMapResponse {
  schemaVersion: number;
  asOf: string;
  electionDay: string;
  daysToElection: number;
  horizon: number;
  /** True when the map is older than the backend's stale threshold. */
  stale?: boolean;
  pollWindowDays: number;
  national: NationalInput;
  bins: { leansMax: number; usuallyMax: number };
  sources: {
    lean: Record<MapChamberKey, LeanSource>;
    training: Record<MapChamberKey, string>;
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
  daysToElection: number;
  horizon: number;
  national: NationalInput & { shift: number; shiftLabel: string };
  stale?: boolean;
  pollWindowDays: number;
  bins: { leansMax: number; usuallyMax: number };
  calibration: ChamberCalibration;
  model: ChamberModel;
  sources: { lean: LeanSource; training: string; polls: PollLicence };
  seats: MapSeat[];
}
