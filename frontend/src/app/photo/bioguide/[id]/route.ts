import { NextRequest } from "next/server";
import { BIOGUIDE_ID, bioguidePhotoUrl } from "@/lib/bioguide";
import { fetchRemoteImage } from "@/lib/remoteImage";

export const runtime = "nodejs";

/**
 * A member's official photo, served from this origin.
 *
 * The share-image capture (`lib/shareImage.ts`) draws a section into a
 * canvas, and a canvas can only read images that are same-origin or sent
 * with CORS headers. bioguide.congress.gov sends none (and answers a
 * browser's own cross-origin fetch with a Cloudflare challenge), so without
 * this route every shared scorecard header would have a hole where the
 * photo is.
 *
 * Takes a bioguide id, never a URL, so it can only ever fetch a photo from
 * bioguide — it is not an open proxy. Not under /api/: nginx sends /api/* to
 * the backend. nginx caches this path (misses too) and rate-limits it, so a
 * loop over made-up ids can't turn the server into a bioguide hammer.
 */
export function parseBioguideId(raw: string): string | null {
  return BIOGUIDE_ID.test(raw) ? raw : null;
}

// Raster formats only: an SVG served from this origin would run script if
// opened directly, and a portrait is never one.
const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const id = parseBioguideId((await params).id);
  if (!id) return notFound();

  const photo = await fetchRemoteImage(bioguidePhotoUrl(id), { types: PHOTO_TYPES });
  // bioguide has no photo for this id: remembered (nginx caches it 10m).
  if (photo.status === "missing") return notFound();
  // Anything transient (timeout, Cloudflare challenge, 5xx) must not be
  // remembered as "no photo": a 502 nginx won't cache, and for which it
  // serves the last good copy it has (proxy_cache_use_stale).
  if (photo.status === "failed") {
    return new Response("Photo unavailable", {
      status: 502,
      headers: { "Cache-Control": "no-store" },
    });
  }

  return new Response(new Uint8Array(photo.bytes), {
    headers: {
      "Content-Type": photo.contentType,
      // Official portraits change about once a term.
      "Cache-Control": "public, max-age=86400",
    },
  });
}

function notFound(): Response {
  return new Response("Not found", {
    status: 404,
    headers: { "Cache-Control": "public, max-age=600" },
  });
}
