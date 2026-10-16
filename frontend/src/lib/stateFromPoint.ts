import { FIPS_TO_STATE } from "@/lib/stateCodes";

/**
 * Which state a point is in, worked out in the browser against the state
 * shapes the site already serves (/data/states-10m.json, us-atlas's
 * unprojected TopoJSON, the election map's own file). Used by "Use my
 * location" (AGENTS.md §8): the coordinates never leave this function, and
 * the only thing kept is the two-letter answer.
 *
 * A small TopoJSON decoder and an even-odd ray cast, rather than two
 * libraries for one lookup. Planar on longitude and latitude, which is
 * exact enough at state scale; a point it can't place (offshore, or the
 * western Aleutians past 180°) returns null and the reader picks instead.
 */

type Ring = [number, number][];

export interface StatesTopology {
  transform: { scale: [number, number]; translate: [number, number] };
  arcs: [number, number][][];
  objects: {
    states: {
      geometries: {
        id?: string;
        type: "Polygon" | "MultiPolygon";
        arcs: number[][] | number[][][];
      }[];
    };
  };
}

function decodeArcs(t: StatesTopology): Ring[] {
  const [sx, sy] = t.transform.scale;
  const [tx, ty] = t.transform.translate;
  return t.arcs.map((arc) => {
    let x = 0;
    let y = 0;
    return arc.map(([dx, dy]) => {
      x += dx;
      y += dy;
      return [x * sx + tx, y * sy + ty] as [number, number];
    });
  });
}

function ring(arcs: Ring[], indexes: number[]): Ring {
  const out: Ring = [];
  for (const i of indexes) {
    const arc = i < 0 ? [...arcs[~i]].reverse() : arcs[i];
    // Consecutive arcs share an end point; drop the duplicate.
    out.push(...(out.length ? arc.slice(1) : arc));
  }
  return out;
}

function inRing([x, y]: [number, number], r: Ring): boolean {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i];
    const [xj, yj] = r[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** The state (USPS code) containing [longitude, latitude], or null. */
export function stateAt(t: StatesTopology, lon: number, lat: number): string | null {
  const arcs = decodeArcs(t);
  for (const g of t.objects.states.geometries) {
    const polygons = (g.type === "Polygon" ? [g.arcs] : g.arcs) as number[][][];
    for (const polygon of polygons) {
      // Even-odd over every ring of the polygon: inside the outer ring and
      // not inside a hole.
      const hits = polygon.filter((r) => inRing([lon, lat], ring(arcs, r))).length;
      if (hits % 2 === 1) return (g.id && FIPS_TO_STATE[g.id]) || null;
    }
  }
  return null;
}

/** Load the site's own shapes (same origin) and place the point. */
export async function stateFromPoint(lon: number, lat: number): Promise<string | null> {
  const res = await fetch("/data/states-10m.json");
  if (!res.ok) return null;
  return stateAt((await res.json()) as StatesTopology, lon, lat);
}
