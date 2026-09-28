import { NextRequest } from "next/server";
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
 * the backend.
 */
export function parseBioguideId(raw: string): string | null {
  return /^[A-Z]\d{6}$/.test(raw) ? raw : null;
}

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const id = parseBioguideId((await params).id);
  if (!id) return new Response("Not found", { status: 404 });

  const photo = await fetchRemoteImage(
    `https://bioguide.congress.gov/bioguide/photo/${id[0]}/${id}.jpg`
  );
  if (!photo) return new Response("Not found", { status: 404 });

  return new Response(new Uint8Array(photo.bytes), {
    headers: {
      "Content-Type": photo.contentType,
      // Official portraits change about once a term.
      "Cache-Control": "public, max-age=86400",
    },
  });
}
