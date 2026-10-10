import { NextRequest } from "next/server";
import { fetchPhoto, parsePhoto } from "@/lib/photos";

export const runtime = "nodejs";

/**
 * A photo, served from this origin: every picture a page shows comes from
 * here (member portraits, justices, Action Center issue photos — see
 * `lib/photos.ts`), so a visitor's browser never requests one from another
 * host, and the share-image capture can read them into a canvas.
 *
 * Takes a kind and an id, never a URL, so it is not an open proxy. Not
 * under /api/: nginx sends /api/* to the backend. nginx caches this path
 * (misses too, and serves a stale copy when the source is down) and
 * rate-limits its cache misses per client, so a loop over made-up ids
 * can't turn the server into a hammer on the photo hosts.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ kind: string; id: string }> }
) {
  const { kind, id } = await params;
  const ref = parsePhoto(kind, id);
  if (!ref) return notFound();

  const photo = await fetchPhoto(ref);
  // The source has no such photo: remembered (nginx caches it 10m).
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
      // Official portraits change about once a term, a stored photo never.
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
