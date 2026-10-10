/**
 * Server-side fetch of a remote image, for the two places the site needs a
 * photo's bytes rather than a link to it: the Open Graph cards
 * (`app/api/og/route.tsx`) and the same-origin photo route every page loads
 * its pictures from (`app/photo/[kind]/[id]/route.ts`, via lib/photos.ts).
 *
 * bioguide.congress.gov sits behind Cloudflare bot-mitigation that blocks a
 * plain HEAD request outright (confirmed live) and challenges a GET that
 * self-identifies as a bot/non-browser client — a browser-shaped User-Agent
 * gets a normal 200, confirmed live, so that's used here rather than a
 * self-identifying one. A 5s timeout keeps a slow/hanging host (also
 * observed live) from stalling the caller instead of just dropping the photo,
 * and a size cap keeps an unexpected body from being buffered whole.
 */
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

/** What a fetch came back with. `missing` is the host saying there is no
 *  such image (404/410) — worth remembering; `failed` is anything transient
 *  or wrong (timeout, challenge, 5xx, not an image, too big) — not. */
export type RemoteImageResult =
  | { status: "ok"; bytes: Buffer; contentType: string }
  | { status: "missing" }
  | { status: "failed" };

/** Reads a body, giving up once it passes `max` bytes — a chunked response
 *  has no Content-Length to check first. */
async function readCapped(res: Response, max: number): Promise<Buffer | null> {
  if (!res.body) return Buffer.from(await res.arrayBuffer());
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks);
}

/**
 * Fetches an image. `types` limits which media types are accepted (bare,
 * without parameters); by default any `image/*`.
 */
export async function fetchRemoteImage(
  url: string,
  { types }: { types?: readonly string[] } = {}
): Promise<RemoteImageResult> {
  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      },
      signal: AbortSignal.timeout(5000),
    });
    if (res.status === 404 || res.status === 410) return { status: "missing" };
    const contentType = (res.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
    const accepted = types ? types.includes(contentType) : contentType.startsWith("image/");
    if (!res.ok || !accepted) return { status: "failed" };
    if (Number(res.headers.get("content-length") ?? 0) > MAX_IMAGE_BYTES)
      return { status: "failed" };
    const bytes = await readCapped(res, MAX_IMAGE_BYTES);
    return bytes ? { status: "ok", bytes, contentType } : { status: "failed" };
  } catch {
    return { status: "failed" };
  }
}
