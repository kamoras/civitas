import { fetchRemoteImage, type RemoteImageResult } from "./remoteImage";

/**
 * Every photo a page shows is served from this site, at
 * `/photo/<kind>/<id>` (`app/photo/[kind]/[id]/route.ts`), so a visitor's
 * browser never requests an image from another host (AGENTS.md §8). The
 * API hands out these paths (backend `app/photos.py`); the route, and the
 * Open Graph renderer, turn one back into the picture server-side.
 *
 * A kind and an id, never a URL: each kind's source is fixed, so nothing a
 * client sends can make the server fetch from a host of its choosing.
 *  - bioguide: a member's official portrait, at a URL built from the id;
 *  - justice, issue: a URL the pipeline stored (an Oyez thumbnail, an
 *    Action Center issue's rights-cleared news photo), looked up by id
 *    from the backend.
 */
const PHOTO_IDS = {
  bioguide: /^[A-Z]\d{6}$/,
  justice: /^[a-z0-9_]{1,64}$/,
  issue: /^\d{1,18}$/,
} as const;

export type PhotoKind = keyof typeof PHOTO_IDS;
export interface PhotoRef {
  kind: PhotoKind;
  id: string;
}

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Raster formats only: an SVG served from this origin would run script if
// opened directly, and a photo is never one.
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

/** A valid kind and id, or null. */
export function parsePhoto(kind: string, id: string): PhotoRef | null {
  if (!Object.prototype.hasOwnProperty.call(PHOTO_IDS, kind)) return null;
  const k = kind as PhotoKind;
  return PHOTO_IDS[k].test(id) ? { kind: k, id } : null;
}

/** The photo a `/photo/<kind>/<id>` path (as the API gives it) names. */
export function parsePhotoPath(path: string): PhotoRef | null {
  const m = /^\/photo\/([a-z]+)\/([^/?#]+)$/.exec(path);
  return m ? parsePhoto(m[1], m[2]) : null;
}

/** Where a photo comes from: a URL, `null` for "no such photo", or
 *  `"failed"` when the backend couldn't be asked. */
async function photoSource({ kind, id }: PhotoRef): Promise<string | null | "failed"> {
  // bioguide.congress.gov/bioguide/photo/<L>/<id>.jpg 301s to this.
  if (kind === "bioguide") return `https://bioguide.congress.gov/photo/${id}.jpg`;
  try {
    const res = await fetch(`${BACKEND}/api/photo-sources/${kind}/${id}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (res.status === 404) return null;
    if (!res.ok) return "failed";
    const { url } = (await res.json()) as { url?: unknown };
    return typeof url === "string" && url.startsWith("https://") ? url : null;
  } catch {
    return "failed";
  }
}

/** The photo's bytes, fetched from its source. */
export async function fetchPhoto(photo: PhotoRef): Promise<RemoteImageResult> {
  const source = await photoSource(photo);
  if (source === null) return { status: "missing" };
  if (source === "failed") return { status: "failed" };
  return fetchRemoteImage(source, { types: PHOTO_TYPES });
}
