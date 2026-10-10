import { NextResponse } from "next/server";
import type { NextFetchEvent, NextRequest } from "next/server";
import { visitSignal } from "@/lib/visitSignal";

// Matches the fallback next.config.mjs uses for its API rewrite: the
// frontend container isn't given BACKEND_URL at runtime, so both rely on
// the `backend` service name, which Swarm's overlay DNS resolves.
const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";

/** Tells the visit counter about the page and router requests it passes
 *  (lib/visitSignal.ts says which, and why both are needed), straight to
 *  the backend over the Docker network. Later pages in the same tab are
 *  reported by NavigationBeacon: a click on a link Next has already
 *  prefetched sends no request at all. */
export function proxy(request: NextRequest, event: NextFetchEvent) {
  const kind = visitSignal(request.headers);
  if (kind) {
    // The X-Real-IP nginx set for this request: see backend/app/api/
    // visits.py's _track_ip() for why the backend trusts it from here.
    const realIp =
      request.headers.get("x-real-ip") ??
      request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
      "";
    const query = new URLSearchParams({ kind, path: request.nextUrl.pathname });
    // Fire-and-forget: the page never waits on the count.
    event.waitUntil(
      fetch(`${BACKEND_URL}/api/track-visit?${query}`, {
        method: "POST",
        headers: { "X-Real-IP": realIp, "User-Agent": request.headers.get("user-agent") ?? "" },
      }).catch(() => {})
    );
  }
  return NextResponse.next();
}

export const config = {
  // data/ is static geometry the maps fetch (public/data/) and photo/ the
  // member photos share images fetch: parts of a page already counted. The
  // feeds are polled on a schedule, which is not anyone visiting, and the
  // icons, sitemaps and robots.txt are fetched by crawlers and feed readers.
  // The admin dashboard is not reader traffic.
  matcher: [
    "/((?!api|_next/static|_next/image|data/|photo/|admin|favicon.ico|icon.svg|apple-icon.png|sitemap.xml|sitemap-index.xml|sitemaps/|robots.txt|opengraph-image|feed\\.xml|feed/).*)",
  ],
};
