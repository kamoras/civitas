/**
 * Server-side fetch of a remote image, for the two places the site needs a
 * photo's bytes rather than a link to it: the Open Graph cards
 * (`app/api/og/route.tsx`) and the same-origin photo route the share-image
 * capture reads (`app/photo/bioguide/[id]/route.ts`).
 *
 * bioguide.congress.gov sits behind Cloudflare bot-mitigation that blocks a
 * plain HEAD request outright (confirmed live) and challenges a GET that
 * self-identifies as a bot/non-browser client — a browser-shaped User-Agent
 * gets a normal 200, confirmed live, so that's used here rather than a
 * self-identifying one. A 5s timeout keeps a slow/hanging host (also
 * observed live) from stalling the caller instead of just dropping the photo.
 */
export async function fetchRemoteImage(
  url: string
): Promise<{ bytes: Buffer; contentType: string } | null> {
  try {
    const res = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      },
      signal: AbortSignal.timeout(5000),
    });
    const contentType = res.headers.get("content-type") ?? "";
    if (!res.ok || !contentType.startsWith("image/")) return null;
    return { bytes: Buffer.from(await res.arrayBuffer()), contentType };
  } catch {
    return null;
  }
}
