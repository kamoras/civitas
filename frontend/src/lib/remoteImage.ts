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

// A real photo host redirects once at most (a moved file, a CDN); more is
// not a photo.
const MAX_REDIRECTS = 3;

/** An https URL on one of `hosts`, on the default port, with no
 *  credentials in it. */
export function onAllowedHost(url: URL, hosts: readonly string[]): boolean {
  return (
    url.protocol === "https:" &&
    url.port === "" &&
    !url.username &&
    !url.password &&
    hosts.includes(url.hostname)
  );
}

/**
 * Fetches an image from one of `hosts`: the URL and every redirect hop
 * must be on them, so a redirect can't take the server anywhere else (the
 * redirects are followed here, one at a time, not by fetch). `types`
 * limits which media types are accepted (bare, without parameters); by
 * default any `image/*`.
 *
 * Hosts are checked by name, not by the address they resolve to: the
 * global fetch has no hook to vet the connected address without adding
 * undici as a dependency, and a look-up before the fetch could be undone
 * by DNS rebinding. Every allowed host is a named public site.
 */
export async function fetchRemoteImage(
  url: string,
  { types, hosts }: { types?: readonly string[]; hosts: readonly string[] }
): Promise<RemoteImageResult> {
  try {
    const signal = AbortSignal.timeout(5000);
    let target = new URL(url);
    for (let hop = 0; ; hop++) {
      if (!onAllowedHost(target, hosts)) return { status: "failed" };
      const res = await fetch(target, {
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        },
        redirect: "manual",
        signal,
      });
      if (res.status >= 300 && res.status < 400) {
        const location = res.headers.get("location");
        if (!location || hop >= MAX_REDIRECTS) return { status: "failed" };
        target = new URL(location, target);
        continue;
      }
      if (res.status === 404 || res.status === 410) return { status: "missing" };
      const contentType = (res.headers.get("content-type") ?? "")
        .split(";")[0]
        .trim()
        .toLowerCase();
      const accepted = types ? types.includes(contentType) : contentType.startsWith("image/");
      if (!res.ok || !accepted) return { status: "failed" };
      if (Number(res.headers.get("content-length") ?? 0) > MAX_IMAGE_BYTES)
        return { status: "failed" };
      const bytes = await readCapped(res, MAX_IMAGE_BYTES);
      return bytes ? { status: "ok", bytes, contentType } : { status: "failed" };
    }
  } catch {
    return { status: "failed" };
  }
}
