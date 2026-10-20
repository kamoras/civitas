/**
 * The election map's display rules. Every figure comes from the
 * backend (GET /api/elections/map); this file only decides how a figure
 * the backend computed is drawn and worded. Nothing here computes a
 * margin, a range or a bin.
 *
 * Wording rule: the map describes how seats usually vote and where polls
 * suggest the margin will fall. It never says who "wins", is "favored" or
 * is "projected", and shows no chance of anyone winning and no seat tally
 * by party. A range is not a call.
 */
import type {
  ElectionMapData,
  ElectionMapResponse,
  MapBin,
  MapChamberKey,
  MapSeat,
  RangedSeat,
} from "@/types/electionMap";

export const MAP_VIEWS = ["usual", "polls", "diff"] as const;
export type MapView = (typeof MAP_VIEWS)[number];
export const MAP_CHAMBERS = ["senate", "house"] as const;
export type MapChamber = MapChamberKey;

export const CHAMBER_LABEL: Record<MapChamber, string> = { senate: "Senate", house: "House" };
const CHAMBER_WORD: Record<MapChamber, string> = { senate: "Senate", house: "House" };

/** "2028 House map": the map's name, from the API's cycle. */
export function mapTitle(cycle: number, chamber: MapChamber): string {
  return `${cycle} ${CHAMBER_WORD[chamber]} map`;
}

/** The lean's presidential elections in words, from the API's window:
 * "2020+2024" -> "2020 and 2024". */
export function leanWindowWords(window: string): string {
  const years = window.match(/\d{4}/g) ?? [];
  return years.length > 1
    ? `${years.slice(0, -1).join(", ")} and ${years[years.length - 1]}`
    : (years[0] ?? window);
}

/** One chamber's map out of the API's response, in the shape every
 * component reads. No figure is computed here. */
export function chamberView(r: ElectionMapResponse, chamber: MapChamber): ElectionMapData {
  const c = r.chambers[chamber];
  return {
    chamber,
    asOf: r.asOf,
    electionDay: r.electionDay,
    cycle: r.cycle,
    daysToElection: r.daysToElection,
    horizon: r.horizon,
    national: { ...r.national, shift: c.shift, shiftLabel: c.shiftLabel },
    stale: r.stale,
    pollWindowDays: r.pollWindowDays,
    // A file built before the field existed was always within its window.
    pollsInRange: r.pollsInRange ?? true,
    bins: r.bins,
    units: r.units,
    calibration: c.calibration,
    model: c.model,
    sources: {
      lean: r.sources.lean[chamber],
      training: r.sources.training[chamber],
      trainingPolls: r.sources.trainingPolls,
      polls: r.sources.polls,
    },
    seats: c.seats,
  };
}

/**
 * In-app links to the map always name the view: /elections/map is a route
 * whose view lives in the query string, and a soft navigation with an
 * empty search reuses the last one (AGENTS.md, "Client-side URL state on
 * statically prerendered routes"), which could open the Difference view
 * on a reader who asked for the map. Do not "tidy" the query away.
 */
export const ELECTION_MAP_HREF = "/elections/map?chamber=senate&view=usual";

export function mapHref(view: MapView, chamber: MapChamber = "senate", seat?: string): string {
  const base = `/elections/map?chamber=${chamber}&view=${view}`;
  return seat ? `${base}&seat=${encodeURIComponent(seat)}` : base;
}

/** The race's own place on its state's ballot page (what /elections/{id}
 * redirects to), linked directly. */
export function racePageHref(seat: MapSeat): string {
  return `/elections/states/${seat.state}#race-${seat.id}`;
}

/** How the seat's poll source answered, in words (pollSource from the
 * backend). An outage or a rejected table never reads as "no poll". */
export function pollSourceSentence(seat: RangedSeat, windowDays: number): string {
  const src = seat.pollSource;
  if (seat.fundamentalsOnly)
    return `Fundamentals only: ${fundamentalsSentence(windowDays, seat.polls.count)}`;
  const kept = src.keptFrom
    ? ` Polls from the last good revision (${formatAsOf(src.keptFrom)}) are used.`
    : "";
  const alone = src.keptFrom
    ? ""
    : " The range is the usual vote with incumbency and the national swing alone.";
  switch (src.state) {
    case "unreachable":
      return `Wikipedia couldn't be reached when this was built (${formatAsOf(src.checkedOn ?? "")}).${kept || alone}`;
    case "rejected":
      return `Wikipedia's poll table for this race failed Civitas's checks on ${formatAsOf(src.checkedOn ?? "")} (${src.detail}).${kept || alone}`;
    case "no_article":
      return `Wikipedia has no article for this race to read polls from.${alone}`;
    case "no_table":
      return `Wikipedia's article for this race lists no poll of these two nominees.${alone}`;
    default:
      return seat.polls.used
        ? `${seat.polls.used} ${seat.polls.used === 1 ? "poll" : "polls"} in the average (the latest from each pollster in the last ${windowDays} days, newest ${seat.polls.latest}); they carry ${Math.round(seat.pollShare * 100)}% of the range's weight, the usual vote the rest.`
        : `No poll of this race in the last ${windowDays} days: the range is the usual vote with incumbency and the national swing alone, which is why it is wide.`;
  }
}

/** Further out than the training polls reach: why the range has no poll
 * in it, and what the listed polls are. */
export function fundamentalsSentence(windowDays: number, listed?: number): string {
  const polls =
    listed === undefined
      ? " Each seat's polls are listed beside its range."
      : listed > 0
        ? " The newest polls are listed below, outside the range."
        : "";
  return `The method's polls were checked only over the final ${windowDays} days before election day, so until then the range is the usual vote with incumbency and the national swing alone.${polls}`;
}

export function parseView(value: string | string[] | undefined | null): MapView {
  const v = Array.isArray(value) ? value[0] : value;
  return (MAP_VIEWS as readonly string[]).includes(v ?? "") ? (v as MapView) : "usual";
}

/** @public The chamber in `?chamber=` (one chamber until the House map lands). */
export function parseChamber(value: string | string[] | undefined | null): MapChamber {
  const v = Array.isArray(value) ? value[0] : value;
  return (MAP_CHAMBERS as readonly string[]).includes(v ?? "") ? (v as MapChamber) : "senate";
}

export const VIEW_LABEL: Record<MapView, string> = {
  usual: "Usual vote",
  polls: "Polls",
  diff: "Difference",
};

export const VIEW_STAMP: Record<MapView, string> = {
  usual: "How each seat usually votes",
  polls: "Margin ranges, from polls where a seat has any",
  diff: "Where polls disagree with the usual vote",
};

export const VIEW_EXPLAINER: Record<MapView, string> = {
  usual:
    "From official results of the last two presidential elections, compared with the country as a whole.",
  polls:
    "The usual vote, nudged by this year's polls where a seat has any. Each seat shows a range of the margin, and close ranges stay grey.",
  diff: "Seats where polls point a different way from how the seat usually votes, by more than the range allows.",
};

/* ── Colour ────────────────────────────────────────────────────────────
   One diverging scale from the party tokens (dem-blue #82acff, rep-red
   #ff8989), stepped by opacity over `surface` (#14110E). Measured
   2026-10-15 against that surface (WCAG 2.x contrast; CIE L*; Machado
   2009 colour-vision simulation, severity 1.0):

     leans    D #4f6590 3.22:1 · R #935250 3.19:1   L* 42.7 / 42.4
     usually  D #6887c5 5.24:1 · R #c76c6b 5.18:1   L* 56.3 / 56.0
     strongly D #82acff 8.33:1 · R #ff8989 8.23:1   L* 70.5 / 70.1
     close    #4c4842 2.06:1 + a 45° hatch of ink-lo (#CDC7BC, 9.9:1)

   Every bin's tile clears 3:1 (WCAG 1.4.11) except "close", which is
   carried by its hatch. Smallest colour difference between a party bin
   and "close": ΔE 30.3 normal vision, 9.6 protanopia, 18.0 deuteranopia,
   24.8 tritanopia; the hatch separates "close" in every case and in
   greyscale. Greyscale separates the steps (L* 31 / 42 / 56 / 70) but
   not the parties, which share each step's lightness; the seat detail
   and the table say D or R in words. No cyan or amber: on the results
   map those mean polls open and a stale feed. */
export const BIN_OPACITY = { leans: 0.54, usually: 0.76, strongly: 1 } as const;
export const DEM_RGB = "130, 172, 255";
export const REP_RGB = "255, 137, 137";
export const CLOSE_FILL = "#4c4842";
export const CLOSE_HATCH = "#CDC7BC";
export const NO_CONTEST_FILL = "rgba(255, 255, 255, 0.05)";

export function binFill(bin: MapBin): string {
  if (bin === "close") return CLOSE_FILL;
  const [side, size] = bin.split("_") as ["D" | "R", keyof typeof BIN_OPACITY];
  return `rgba(${side === "D" ? DEM_RGB : REP_RGB}, ${BIN_OPACITY[size]})`;
}

/** The bin a seat shows in a view; null where it has none (no
 * Democrat-versus-Republican contest, or the Difference view). */
export function seatBin(seat: MapSeat, view: MapView): MapBin | null {
  if (view === "usual") return seat.usualBin ?? null;
  if (view === "polls") return seat.status === "range" ? seat.pollsBin : null;
  return null;
}

/** The legend's cells, D side to R side, with their labels. */
export function legendCells(
  bins: ElectionMapData["bins"],
  view: MapView
): { bin: MapBin; label: string }[] {
  const lo = bins.leansMax;
  const hi = bins.usuallyMax;
  return [
    { bin: "D_strongly", label: `D ${hi}+` },
    { bin: "D_usually", label: `D ${lo}–${hi}` },
    { bin: "D_leans", label: `D <${lo}` },
    { bin: "close", label: view === "polls" ? "Too close to tell" : "EVEN" },
    { bin: "R_leans", label: `R <${lo}` },
    { bin: "R_usually", label: `R ${lo}–${hi}` },
    { bin: "R_strongly", label: `R ${hi}+` },
  ];
}

/* ── Words ─────────────────────────────────────────────────────────── */

/* Every label and every "too close" below is the backend's (build.py):
   the page never re-rounds a number, so a label can't disagree with the
   colour the backend chose for it. */

/** "D+8 to R+2": the seat's 80% range, D side first. */
export function rangeLabel(seat: RangedSeat): string {
  return seat.range80Label;
}

export function crossesEven(seat: RangedSeat): boolean {
  return seat.close;
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
}

/** The seat's name on the map: "Ohio", "Florida (special)", "Ohio's 9th
 * district", "Alaska at-large". */
export function seatName(seat: MapSeat): string {
  if (seat.district != null)
    return seat.district === 0
      ? `${seat.stateName} at-large`
      : `${seat.stateName}'s ${ordinal(seat.district)} district`;
  return seat.special ? `${seat.stateName} (special)` : seat.stateName;
}

/** "80% ranges held the result 79% of the time on cycles the method never
 * saw (419 Senate races, 1998–2022)." — the evidence behind every range. */
export function coverageSentence(
  c: ElectionMapData["calibration"],
  chamber: MapChamber = "senate",
  fundamentalsOnly = false
): string {
  const share =
    fundamentalsOnly && c.coverage80Fundamentals != null ? c.coverage80Fundamentals : c.coverage80;
  const kind = fundamentalsOnly ? "Fundamentals-only ranges" : "Ranges like these";
  return `${kind} held the result ${Math.round(share * 100)}% of the time on election cycles the method never saw (${c.races} ${CHAMBER_WORD[chamber]} races, ${c.cycles[0]}–${c.cycles[1]}).`;
}

export function chamberWord(chamber: MapChamber): string {
  return CHAMBER_WORD[chamber];
}

/** The national line in the legend: what the generic ballot does to every
 * seat at once. */
export function nationalSentence(n: ElectionMapData["national"]): string {
  const when =
    n.status === "ok"
      ? `updated ${formatAsOf(n.updated)}`
      : `last good reading, updated ${formatAsOf(n.updated)}: the latest could not be read`;
  return `Nationally, the generic-ballot average is ${n.label} (${when}); in the method that moves every seat about ${n.shiftLabel} at once.`;
}

export interface MapCounts {
  close: number;
  differ: number;
  towardD: number;
  towardR: number;
  ranged: number;
  usualClose: number;
}

export function mapCounts(seats: MapSeat[]): MapCounts {
  const ranged = seats.filter((s): s is RangedSeat => s.status === "range");
  const differ = ranged.filter((s) => s.difference.strength > 0);
  return {
    ranged: ranged.length,
    close: ranged.filter((s) => s.pollsBin === "close").length,
    differ: differ.length,
    towardD: differ.filter((s) => s.difference.direction === "D").length,
    towardR: differ.filter((s) => s.difference.direction === "R").length,
    usualClose: seats.filter(
      (s) => s.usualBin === "close" || s.usualBin === "D_leans" || s.usualBin === "R_leans"
    ).length,
  };
}

/** The "what changed" line for a view — counts of seats that differ or
 * are close, never of seats a party is ahead in. */
export function changedLine(
  view: MapView,
  c: MapCounts,
  n: ElectionMapData["national"],
  bins: ElectionMapData["bins"]
): string {
  const seats = (k: number) => `${k} ${k === 1 ? "seat" : "seats"}`;
  if (view === "usual")
    return `${seats(c.usualClose)} within ${bins.leansMax} points of even in the usual vote.`;
  if (view === "polls")
    return `vs usual vote: ${seats(c.differ)} look different · national swing ${n.shiftLabel} · ${seats(c.close)} too close to tell`;
  return `${seats(c.differ)} where polls disagree with the usual vote · ${c.towardD} toward D · ${c.towardR} toward R`;
}

/** What the mode stamp's live region announces (and the sighted reader
 * sees under the stamp). */
export function stampSentence(view: MapView, c: MapCounts, asOf: string): string {
  const date = formatAsOf(asOf);
  if (view === "usual") return `Showing how each seat usually votes. As of ${date}.`;
  if (view === "polls")
    return `Showing margin ranges, from polls where a seat has any. ${c.close} ${c.close === 1 ? "seat" : "seats"} too close to tell. As of ${date}.`;
  return `Showing where polls disagree with the usual vote: ${c.differ} ${c.differ === 1 ? "seat" : "seats"}. As of ${date}.`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "15 Oct 2026" from the backend's ISO time (UTC date). */
export function formatAsOf(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** The footer every view and every share image carries. */
export function footerText(data: ElectionMapData): string {
  const usual =
    data.chamber === "house"
      ? "Civitas, from precinct returns (VEST, MEDSL) and Census blocks"
      : "Civitas, from official returns (MEDSL, FEC)";
  const training = data.sources.trainingPolls
    ? ` Past polls the ranges are fitted on: ${data.sources.trainingPolls.attribution}.`
    : "";
  const fundamentals = data.pollsInRange ? "" : " Fundamentals only, no poll in the ranges.";
  return `${data.stale ? "Not updated since " + formatAsOf(data.asOf) + ". " : ""}Usual vote: ${usual}. Polls: ${data.sources.polls.attribution}, ${data.sources.polls.license}.${training}${fundamentals} As of ${formatAsOf(data.asOf)}. Ranges, not results. Not a race call.`;
}

/** The Difference view's glyph for a seat: « ‹ toward D, › » toward R. */
export function differenceGlyph(seat: MapSeat): string | null {
  if (seat.status !== "range" || seat.difference.strength === 0) return null;
  const d = seat.difference.direction === "D";
  return seat.difference.strength === 2 ? (d ? "«" : "»") : d ? "‹" : "›";
}

/** One seat's accessible name on the map. */
export function seatAriaLabel(seat: MapSeat, view: MapView): string {
  const usual = seat.leanLabel ? `usual vote ${seat.leanLabel} lean` : "no usual-vote figure";
  if (seat.status !== "range") return `${seatName(seat)}: ${usual}. ${seat.reason}`;
  // A file written before the label existed (the minutes between a deploy
  // and the next build) names the range by its unit alone.
  const polls = `${seat.rangeBasisLabel ?? "Margin range"}: ${rangeLabel(seat)}${crossesEven(seat) ? ", too close to tell" : ""}`;
  const diff =
    seat.difference.strength > 0
      ? `, polls point toward ${seat.difference.direction}`
      : seat.difference.seatPolls
        ? ""
        : ", no seat poll";
  return `${seatName(seat)}: ${view === "usual" ? usual : `${polls}; ${usual}`}${view === "diff" ? diff : ""}.`;
}

/** A state's name on the House map: "Ohio: 15 seats, 3 too close to tell". */
export function stateAriaLabel(
  stateName: string,
  seats: MapSeat[],
  view: MapView,
  leansMax: number
): string {
  const n = seats.length;
  const head = `${stateName}: ${n} ${n === 1 ? "seat" : "seats"}`;
  if (view === "usual") {
    const close = seats.filter(
      (s) => s.usualBin === "close" || s.usualBin === "D_leans" || s.usualBin === "R_leans"
    ).length;
    return `${head}, ${close} within ${leansMax} points of even in the usual vote.`;
  }
  if (view === "polls") {
    const close = seats.filter((s) => s.status === "range" && s.pollsBin === "close").length;
    return `${head}, ${close} too close to tell.`;
  }
  const differ = seats.filter((s) => s.status === "range" && s.difference.strength > 0).length;
  return `${head}, ${differ} where polls disagree with the usual vote.`;
}

/* ── House tile layout: equal-size tiles, one per seat, each state a
   near-square block, the blocks in rows by STATE_TILES' rough geography. ─ */

/** What the House layout needs of a seat: where it is. The swing map
 * (archive) lays out its own seat rows with it too. */
export interface TileSeat {
  state: string;
  district?: number | null;
}

export interface HouseBlock<T extends TileSeat = MapSeat> {
  state: string;
  x: number;
  y: number;
  cols: number;
  rows: number;
  seats: T[];
}

export const HOUSE_TILE = 10;
export const HOUSE_GAP = 1;
const BLOCK_GAP = 6;
const LABEL_H = 7;
const ROW_GAP = 4;

/** Block positions (SVG units) and the canvas size. Pure layout: the same
 * seats always give the same picture. */
export function houseLayout<T extends TileSeat = MapSeat>(
  seats: T[]
): {
  blocks: HouseBlock<T>[];
  width: number;
  height: number;
} {
  const byState = new Map<string, T[]>();
  for (const s of seats) byState.set(s.state, [...(byState.get(s.state) ?? []), s]);
  for (const list of byState.values()) list.sort((a, b) => (a.district ?? 0) - (b.district ?? 0));
  const step = HOUSE_TILE + HOUSE_GAP;
  const rows = new Map<number, { state: string; col: number }[]>();
  for (const st of byState.keys()) {
    const pos = STATE_TILES[st];
    if (!pos) continue;
    rows.set(pos[1], [...(rows.get(pos[1]) ?? []), { state: st, col: pos[0] }]);
  }
  const placed: HouseBlock<T>[] = [];
  let y = 0;
  let width = 0;
  const rowBlocks: HouseBlock<T>[][] = [];
  for (const r of [...rows.keys()].sort((a, b) => a - b)) {
    let x = 0;
    let tallest = 0;
    const line: HouseBlock<T>[] = [];
    for (const { state } of rows.get(r)!.sort((a, b) => a.col - b.col)) {
      const list = byState.get(state)!;
      const cols = Math.ceil(Math.sqrt(list.length));
      const rowsN = Math.ceil(list.length / cols);
      const block = { state, x, y: y + LABEL_H, cols, rows: rowsN, seats: list };
      line.push(block);
      x += cols * step - HOUSE_GAP + BLOCK_GAP;
      tallest = Math.max(tallest, rowsN);
    }
    width = Math.max(width, x - BLOCK_GAP);
    rowBlocks.push(line);
    y += LABEL_H + tallest * step + ROW_GAP;
  }
  // Centre each row on the widest.
  for (const line of rowBlocks) {
    const last = line[line.length - 1];
    const w = last.x + last.cols * step - HOUSE_GAP;
    const shift = (width - w) / 2;
    for (const b of line) placed.push({ ...b, x: b.x + shift });
  }
  return { blocks: placed, width, height: y - ROW_GAP };
}

/* ── Tile layout: one tile per state, roughly where the state sits ──── */

export const STATE_TILES: Record<string, [col: number, row: number]> = {
  AK: [0, 0],
  ME: [11, 0],
  VT: [10, 1],
  NH: [11, 1],
  WA: [1, 2],
  ID: [2, 2],
  MT: [3, 2],
  ND: [4, 2],
  MN: [5, 2],
  IL: [6, 2],
  WI: [7, 2],
  MI: [8, 2],
  NY: [9, 2],
  RI: [10, 2],
  MA: [11, 2],
  OR: [1, 3],
  NV: [2, 3],
  WY: [3, 3],
  SD: [4, 3],
  IA: [5, 3],
  IN: [6, 3],
  OH: [7, 3],
  PA: [8, 3],
  NJ: [9, 3],
  CT: [10, 3],
  CA: [1, 4],
  UT: [2, 4],
  CO: [3, 4],
  NE: [4, 4],
  MO: [5, 4],
  KY: [6, 4],
  WV: [7, 4],
  VA: [8, 4],
  MD: [9, 4],
  DE: [10, 4],
  AZ: [2, 5],
  NM: [3, 5],
  KS: [4, 5],
  AR: [5, 5],
  TN: [6, 5],
  NC: [7, 5],
  SC: [8, 5],
  DC: [9, 5],
  OK: [4, 6],
  LA: [5, 6],
  MS: [6, 6],
  AL: [7, 6],
  GA: [8, 6],
  HI: [1, 7],
  TX: [4, 7],
  FL: [9, 7],
};
export const TILE_COLS = 12;
export const TILE_ROWS = 8;
