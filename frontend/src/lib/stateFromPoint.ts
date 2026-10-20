import { featureAt, type Topology } from "@/lib/districtAt";
import { FIPS_TO_STATE } from "@/lib/stateCodes";

/**
 * Which state a point is in, worked out in the browser against the state
 * shapes the site already serves (/data/states-10m.json, us-atlas's
 * unprojected TopoJSON, the election map's own file). Used by "Use my
 * location" (AGENTS.md §8): the coordinates never leave this function, and
 * the only thing kept is the two-letter answer.
 *
 * The decoder and ray cast are districtAt.ts's. Planar on longitude and
 * latitude, which is exact enough at state scale; a point it can't place (offshore, or the
 * western Aleutians past 180°) returns null and the reader picks instead.
 */

export type StatesTopology = Topology;

/** The state (USPS code) containing [longitude, latitude], or null. */
export function stateAt(t: StatesTopology, lon: number, lat: number): string | null {
  const id = featureAt(t, "states", lon, lat)?.id;
  return (id && FIPS_TO_STATE[id]) || null;
}

/** Load the site's own shapes (same origin) and place the point. */
export async function stateFromPoint(lon: number, lat: number): Promise<string | null> {
  const res = await fetch("/data/states-10m.json");
  if (!res.ok) return null;
  return stateAt((await res.json()) as StatesTopology, lon, lat);
}
