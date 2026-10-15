import { NextRequest } from "next/server";
import { photoResponse } from "@/lib/photos";

export const runtime = "nodejs";

/**
 * A photo scaled down to one of `PHOTO_WIDTHS` (lib/photos.ts), as WebP:
 * what `Photo`'s srcset asks for, so a 341px slot never downloads a 1620px
 * original. The width is in the path, not a query string, because nginx
 * keys this cache on the path alone; any other width is a 404. Same id-only
 * rule, cache and rate limit as `/photo/<kind>/<id>`.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ kind: string; id: string; width: string }> }
) {
  const { kind, id, width } = await params;
  return photoResponse(kind, id, width);
}
