import type { ReadoutMark } from "./readout";

/** GET /api/presidents/{id}/charts (backend app/president_charts.py). Every
 *  value, label and sentence is the backend's; the page only draws them. */
interface ChartBase {
  key: string;
  title: string;
  /** What the chart answers, in one plain line. */
  why: string;
  xLabel: string;
  yLabel: string;
  /** The span and era the comparison covers. */
  window: string;
  source: { name: string; url: string };
  caption: string;
  yMin: number;
  yMax: number;
  /** The axis ends, as printed. */
  yTop: string;
  yBottom: string;
  /** Other presidencies compared. */
  n: number;
}

export interface LineChart extends ChartBase {
  kind: "line";
  series: { id: string; name: string; points: [number, number][] };
  others: { id: string; name: string; points: [number, number][] }[];
  /** The others' middle half at each point at least `minBand` of them reach. */
  band: { x: number; low: number; median: number; high: number; n: number }[];
  xMax: number;
  /** The accessible form: this term's points, and the others' middle half there. */
  table: { x: number; value: string; band: string | null }[];
  /** One readout per point of `series`, in order: value and the band there. */
  marks: ReadoutMark[];
}

export interface BarsChart extends ChartBase {
  kind: "bars";
  bars: { label: string; value: number; shown: string; first: boolean }[];
  eraBand: {
    low: number;
    median: number;
    high: number;
    years: number;
    presidencies: number;
  } | null;
  bandShown: string | null;
  /** One readout per bar, in order. */
  marks: ReadoutMark[];
}

export interface ElectionsChart extends ChartBase {
  kind: "elections";
  /** The popular-margin source, beside `source` (electoral votes). */
  source2: { name: string; url: string };
  /** Every election, oldest first; `mine` for this presidency's. */
  rows: {
    year: number;
    president: string;
    presidentId: string;
    evShare: number;
    evShown: string;
    popularMargin: number | null;
    marginShown: string;
    mine: boolean;
    decidedByHouse: boolean;
  }[];
  medianShare: number;
  medianShareShown: string;
  medianMargin: number | null;
  /** One readout per election (id the year), in `rows` order. */
  marks: ReadoutMark[];
}

export type PresidentChart = LineChart | BarsChart | ElectionsChart;

export interface PresidentCharts {
  charts: PresidentChart[];
  minBand: number;
}
