/** GET /api/elections/archive/{cycle}/accountability (backend
 * app/forecast/accountability.py): the frozen pre-election map's ranges
 * against the counts their states list as official, and each seat's swing
 * from its usual vote. Every figure and label is the backend's. */
import type { MapBin, MapChamberKey } from "@/types/electionMap";

/** official: the source lists it official (and it is D vs R); counting: a
 * count not yet official; no_count: none read; not_two_party: official,
 * but its top two are not a Democrat and a Republican. */
export type CountState = "official" | "counting" | "no_count" | "not_two_party";

export interface AccountabilitySeat {
  id: string;
  chamber: MapChamberKey;
  state: string;
  stateName: string;
  district: number | null;
  special: boolean;
  /** The map's own status for the seat ("range" when it had one). */
  mapStatus: string;
  count: CountState;
  dem: string | null;
  rep: string | null;
  lean: number | null;
  leanLabel: string | null;
  rangeBasis: string | null;
  rangeBasisShort: string | null;
  range80: [number, number] | null;
  range95: [number, number] | null;
  range80Label: string | null;
  range95Label: string | null;
  sourceName: string | null;
  sourceUrl: string | null;
  fetchedAt: string | null;
  margin: number | null;
  marginLabel: string | null;
  usualMargin: number | null;
  usualMarginLabel: string | null;
  swing: number | null;
  swingLabel: string | null;
  swingBin: MapBin | null;
  inside80: boolean | null;
  inside95: boolean | null;
  fromCentre: number | null;
}

export interface CoverageCell {
  graded: number;
  held80: number;
  share80: number | null;
  /** Exact binomial (Clopper-Pearson) interval, as shares. */
  interval80: [number, number] | null;
  held95: number;
  share95: number | null;
  interval95: [number, number] | null;
}

export interface BasisCell extends CoverageCell {
  basis: string;
  label: string;
  ranged: number;
}

export interface ChamberAccountability {
  /** Seats the map gave a range. */
  ranged: number;
  /** Of those, counts their source lists official. */
  certified: number;
  counts: Record<CountState, number>;
  all: CoverageCell;
  byBasis: BasisCell[];
  promised: {
    nominal80: number;
    nominal95: number;
    heldOut: {
      cycles: [number, number] | null;
      races: number | null;
      coverage80: number | null;
      coverage95: number | null;
      coverage80Fundamentals: number | null;
    };
    forward: {
      cycle: number;
      races: number;
      coverage80: number;
      coverage95: number;
    } | null;
  };
  /** Furthest outside the 80% range first; outsideBy in margin points. */
  misses: (AccountabilitySeat & { outsideBy: number })[];
  swing: {
    seats: number;
    mean: number | null;
    meanLabel: string | null;
    median: number | null;
    medianLabel: string | null;
  };
  seats: AccountabilitySeat[];
}

export interface MapAccountability {
  cycle: number;
  electionDay: string;
  mapAsOf: string;
  units: { lean: string; margin: string };
  bins: { leansMax: number; usuallyMax: number };
  intervalLevel: number;
  stillCounting: boolean;
  chambers: Partial<Record<MapChamberKey, ChamberAccountability>>;
}

/** The archive index's line per cycle (null where no map was kept). */
export interface AccountabilitySummary {
  ranged: number;
  certified: number;
  official: number;
  graded: number;
  held80: number;
  held95: number;
  swingSeats: number;
}
