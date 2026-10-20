import sharp from "sharp";
import { fetchRemoteImage, onAllowedHost, type RemoteImageResult } from "./remoteImage";

/**
 * Every photo a page shows is served from this site, at
 * `/photo/<kind>/<id>` (`app/photo/[kind]/[id]/route.ts`), so a visitor's
 * browser never requests an image from another host (AGENTS.md §8). The
 * API hands out these paths (backend `app/photos.py`); the route, and the
 * Open Graph renderer, turn one back into the picture server-side.
 *
 * A kind and an id, never a URL: each kind's source is fixed, so nothing a
 * client sends can make the server fetch from a host of its choosing.
 * Each kind's source URL is looked up by id from the backend:
 *  - bioguide: a member's official photo, the headshot on the member's
 *    Congress.gov record or else bioguide's portrait (backend
 *    `bioguide_photo_source`);
 *  - president: a public-domain portrait on Wikimedia Commons the
 *    pipeline found through Wikidata (backend president_portraits.py);
 *  - justice, issue: a URL the pipeline stored (an Oyez thumbnail, an
 *    Action Center issue's rights-cleared news photo).
 */
const PHOTO_IDS = {
  bioguide: /^[A-Z]\d{6}$/,
  justice: /^[a-z0-9_]{1,64}$/,
  issue: /^\d{1,18}$/,
  // A president's id ("surname-number"), as in their page's address.
  president: /^[a-z0-9-]{1,64}$/,
} as const;

export type PhotoKind = keyof typeof PHOTO_IDS;
export interface PhotoRef {
  kind: PhotoKind;
  id: string;
}

/**
 * The only hosts each kind's photo is fetched from: the URL the backend
 * names and every redirect hop. A copy of backend `PHOTO_SOURCE_HOSTS`
 * (app/photos.py, which says where each list comes from), held equal by
 * backend/tests/test_same_origin_photos.py. An issue's photo URL is read
 * from an RSS item, so without this a feed could point the server at any
 * host.
 */
export const PHOTO_HOSTS: Record<PhotoKind, readonly string[]> = {
  bioguide: ["www.congress.gov", "bioguide.congress.gov"],
  justice: ["api.oyez.org"],
  issue: ["rollcall.com"],
  president: ["thumb.wikimedia.org", "upload.wikimedia.org"],
};

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
  try {
    const res = await fetch(`${BACKEND}/api/photo-sources/${kind}/${id}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (res.status === 404) return null;
    if (!res.ok) return "failed";
    const { url } = (await res.json()) as { url?: unknown };
    return typeof url === "string" && URL.canParse(url) ? url : null;
  } catch {
    return "failed";
  }
}

/** The photo's bytes, fetched from its source. */
export async function fetchPhoto(photo: PhotoRef): Promise<RemoteImageResult> {
  const source = await photoSource(photo);
  if (source === null) return { status: "missing" };
  if (source === "failed") return { status: "failed" };
  const hosts = PHOTO_HOSTS[photo.kind];
  // A source off the kind's hosts is refused as "no such photo" (a cached
  // 404): the backend should never name one, and retrying won't change it.
  if (!onAllowedHost(new URL(source), hosts)) return { status: "missing" };
  return fetchRemoteImage(source, { types: PHOTO_TYPES, hosts });
}

/**
 * The widths a photo is served at, `/photo/<kind>/<id>/<width>`: a fixed
 * few, so nginx's cache (keyed on the path) holds at most this many copies
 * of a photo and a client can't mint new entries by asking for others.
 * Chosen for the slots that show photos at 1x to 3x: a 40px directory
 * portrait or 64px issue thumbnail (96), a 96px profile portrait (192), a
 * phone-width issue photo (400, and 1200 at 3x) and a desktop one (800).
 * An issue photo's source is 1620px and 364 KB, shown 341px wide on a
 * phone: the worst LCP on the site (4.1s at 375px, 2026-10-14).
 */
export const PHOTO_WIDTHS = [96, 192, 400, 800, 1200] as const;

/** A width from the list, or null. */
export function parsePhotoWidth(raw: string): number | null {
  const w = Number(raw);
  return (PHOTO_WIDTHS as readonly number[]).includes(w) && String(w) === raw ? w : null;
}

// WebP at this quality: about a tenth of the source JPEG's bytes at 400px,
// with no artefacts visible at the size shown.
const WEBP_QUALITY = 75;

/**
 * The response for `/photo/<kind>/<id>` (the source's own bytes) and
 * `/photo/<kind>/<id>/<width>` (scaled down to `width`, never up, as WebP).
 * nginx caches by status (200 for a day, 404 for ten minutes, a 502 never),
 * so the status each outcome maps to is the contract.
 */
export async function photoResponse(kind: string, id: string, width?: string): Promise<Response> {
  const ref = parsePhoto(kind, id);
  const w = width === undefined ? null : parsePhotoWidth(width);
  if (!ref || (width !== undefined && w === null)) return notFoundResponse();

  const photo = await fetchPhoto(ref);
  // The source has no such photo: remembered (nginx caches it 10m).
  if (photo.status === "missing") return notFoundResponse();
  // Anything transient (timeout, Cloudflare challenge, 5xx) must not be
  // remembered as "no photo": a 502 nginx won't cache, and for which it
  // serves the last good copy it has (proxy_cache_use_stale).
  if (photo.status === "failed") return unavailableResponse();

  let bytes: Uint8Array<ArrayBuffer> = new Uint8Array(photo.bytes);
  let contentType = photo.contentType;
  if (w !== null) {
    try {
      bytes = new Uint8Array(
        await sharp(photo.bytes)
          .rotate() // EXIF orientation, before the metadata is dropped
          .resize({ width: w, withoutEnlargement: true })
          .webp({ quality: WEBP_QUALITY })
          .toBuffer()
      );
      contentType = "image/webp";
    } catch {
      // Bytes that claimed an image type but don't decode: not remembered,
      // as for any other failed fetch.
      return unavailableResponse();
    }
  }
  return new Response(bytes, {
    headers: {
      "Content-Type": contentType,
      // Official portraits change about once a term, a stored photo never.
      "Cache-Control": "public, max-age=86400",
    },
  });
}

function notFoundResponse(): Response {
  return new Response("Not found", {
    status: 404,
    headers: { "Cache-Control": "public, max-age=600" },
  });
}

function unavailableResponse(): Response {
  return new Response("Photo unavailable", {
    status: 502,
    headers: { "Cache-Control": "no-store" },
  });
}
