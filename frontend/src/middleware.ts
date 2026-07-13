import { NextResponse } from "next/server";
import type { NextFetchEvent, NextRequest } from "next/server";

// Matches the fallback next.config.mjs already uses for its API rewrite —
// the frontend container isn't given BACKEND_URL at runtime, so both rely
// on the Docker network alias set up by deploy.sh.
const BACKEND_URL = process.env.BACKEND_URL || "http://backend:8000";

export function middleware(request: NextRequest, event: NextFetchEvent) {
  // Relay the X-Real-IP nginx already set for this request — see
  // backend/app/api/visits.py's _track_ip() for why the backend trusts it
  // coming from here specifically (this call bypasses nginx, going
  // straight to the backend over the internal Docker network).
  const realIp =
    request.headers.get("x-real-ip") ??
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "";
  const userAgent = request.headers.get("user-agent") ?? "";

  // Civitas's own link-card fetcher (backend/app/pipeline/analyze/
  // bluesky_utils.py's build_link_card) requests the site's own pages to
  // scrape OG metadata for Bluesky posts, self-identifying with this UA —
  // don't count the app visiting itself as a visitor.
  if (userAgent.startsWith("Civitas-Bot/")) {
    return NextResponse.next();
  }

  // Fire-and-forget: don't block the page response on this.
  const path = encodeURIComponent(request.nextUrl.pathname);
  event.waitUntil(
    fetch(`${BACKEND_URL}/api/track-visit?path=${path}`, {
      method: "POST",
      headers: { "X-Real-IP": realIp, "User-Agent": userAgent },
    }).catch(() => {}),
  );

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!api|_next/static|_next/image|admin|favicon.ico|icon.svg|sitemap.xml|robots.txt|opengraph-image).*)",
  ],
};
