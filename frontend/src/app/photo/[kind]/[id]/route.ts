import { NextRequest } from "next/server";
import { photoResponse } from "@/lib/photos";

export const runtime = "nodejs";

/**
 * A photo, served from this origin: every picture a page shows comes from
 * here (member portraits, justices, Action Center issue photos — see
 * `lib/photos.ts`), so a visitor's browser never requests one from another
 * host, and the share-image capture can read them into a canvas. This path
 * is the source's own bytes; `./[width]` is the same photo scaled down,
 * which is what pages show (Photo's srcset).
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
  return photoResponse(kind, id);
}
