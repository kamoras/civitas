/**
 * Which district a point is in, worked out in the browser against the
 * site's own shapes (AGENTS.md §8). The coordinates never leave these
 * functions: the page fetches a state's whole file from this site, with no
 * query, and places the point itself. Only the answer (a district number)
 * is kept, by the caller.
 *
 * The files, built and checked against every 2020 census block by
 * backend/scripts/build_district_lookup.py, are named by the lines they
 * hold: congress-<N>/<ST>.json, the House districts the members of that
 * Congress were (or will be) elected in, with <ST>.towns.json beside it
 * (the towns split between them and their roads, for the town map); and
 * election-<year>/<ST>.legislature.json, the state senate and house
 * districts that year's election is on. A page asks for the map it names,
 * never another: in a state that redrew, the same number is a different
 * district on each.
 *
 * A small TopoJSON decoder and an even-odd ray cast, rather than two
 * libraries for one lookup. Planar on longitude and latitude, which is
 * exact enough at this scale: the build measures the error.
 */

export type Point = [number, number];
export type Ring = Point[];

export interface TopoGeometry {
  id?: string;
  type: "Polygon" | "MultiPolygon" | "LineString" | "MultiLineString" | null;
  arcs?: number[] | number[][] | number[][][];
  properties?: { d?: number | string; n?: string };
}

export interface Topology {
  transform: { scale: [number, number]; translate: [number, number] };
  arcs: [number, number][][];
  objects: Record<string, { geometries: TopoGeometry[] }>;
}

export function decodeArcs(t: Topology): Ring[] {
  const [sx, sy] = t.transform.scale;
  const [tx, ty] = t.transform.translate;
  return t.arcs.map((arc) => {
    let x = 0;
    let y = 0;
    return arc.map(([dx, dy]) => {
      x += dx;
      y += dy;
      return [x * sx + tx, y * sy + ty] as Point;
    });
  });
}

function join(arcs: Ring[], indexes: number[]): Ring {
  const out: Ring = [];
  for (const i of indexes) {
    const arc = i < 0 ? [...arcs[~i]].reverse() : arcs[i];
    // Consecutive arcs share an end point; drop the duplicate.
    out.push(...(out.length ? arc.slice(1) : arc));
  }
  return out;
}

/** A polygon geometry's polygons, each a list of rings (outer, then holes). */
export function polygonsOf(arcs: Ring[], g: TopoGeometry): Ring[][] {
  if (g.type === "Polygon") return [(g.arcs as number[][]).map((r) => join(arcs, r))];
  if (g.type === "MultiPolygon")
    return (g.arcs as number[][][]).map((p) => p.map((r) => join(arcs, r)));
  return [];
}

/** A line geometry's lines. */
export function linesOf(arcs: Ring[], g: TopoGeometry): Ring[] {
  if (g.type === "LineString") return [join(arcs, g.arcs as number[])];
  if (g.type === "MultiLineString") return (g.arcs as number[][]).map((l) => join(arcs, l));
  return [];
}

function inRing([x, y]: Point, r: Ring): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i];
    const [xj, yj] = r[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Inside one of the polygons: inside its outer ring and not in a hole. */
export function inPolygons(p: Point, polygons: Ring[][]): boolean {
  return polygons.some((rings) => rings.filter((r) => inRing(p, r)).length % 2 === 1);
}

/** The first geometry of `object` containing [longitude, latitude]. */
export function featureAt(
  t: Topology,
  object: string,
  lon: number,
  lat: number,
  arcs: Ring[] = decodeArcs(t)
): TopoGeometry | null {
  for (const g of t.objects[object]?.geometries ?? []) {
    if (inPolygons([lon, lat], polygonsOf(arcs, g))) return g;
  }
  return null;
}

export interface DistrictsAtPoint {
  /** U.S. House district number, 0 at large; null when the point is in none. */
  house: number | null;
  /** State senate and house district keys, where the file has them. */
  sldu: string | null;
  sldl: string | null;
}

/** Every district in the file containing the point. */
export function districtsAt(t: Topology, lon: number, lat: number): DistrictsAtPoint {
  const arcs = decodeArcs(t);
  const key = (object: string) => featureAt(t, object, lon, lat, arcs)?.properties?.d ?? null;
  const house = key("house");
  const sldu = key("sldu");
  const sldl = key("sldl");
  return {
    house: house == null ? null : Number(house),
    sldu: sldu == null ? null : String(sldu),
    sldl: sldl == null ? null : String(sldl),
  };
}

/** "congress-119" or "election-2026": the only names the folders go by. */
export const LOOKUP_LINES = /^(congress-\d{1,3}|election-\d{4})$/;

/** "house": one Congress's House districts; "towns": the towns split
 * between them, with their main roads; "legislature": the state senate and
 * house districts a cycle's election is on. */
export type LookupKind = "house" | "towns" | "legislature";

/** A state's file for one map, from this site; null when there is none
 * (a Congress or a cycle nobody has built yet). No query, no body. */
export async function loadDistrictLookup(
  lines: string,
  state: string,
  kind: LookupKind = "house"
): Promise<Topology | null> {
  if (!LOOKUP_LINES.test(lines) || !/^[A-Z]{2}$/.test(state)) return null;
  const res = await fetch(
    `/data/lookup/${lines}/${state}${kind === "house" ? "" : `.${kind}`}.json`
  );
  if (!res.ok) return null;
  return (await res.json()) as Topology;
}

/** The Congress whose House lines a cycle's ballot is on: the one the
 * November election seats (2026 seats the 120th; the 1st was elected in
 * 1788) when the state votes on new lines, else the sitting one's, which
 * are the same lines. */
export function ballotHouseLines(cycleYear: number, newDistrictLines: boolean): string {
  const seated = (cycleYear - 1786) / 2;
  return `congress-${newDistrictLines ? seated : seated - 1}`;
}
