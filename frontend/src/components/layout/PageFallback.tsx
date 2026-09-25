import React from "react";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";

/**
 * What the server sends for a page that cannot be server-rendered.
 *
 * /action, /leaderboard, /bills, /explore and /compare each read
 * `useSearchParams()` — view state lives in the address bar — and that hook
 * has no value during SSR. Next.js handles this by suspending, so whatever
 * sits inside the nearest <Suspense> renders its FALLBACK on the server and
 * the real subtree only after hydration.
 *
 * Every one of those five pages wrapped its entire body — navbar, masthead,
 * content, footer — in `<Suspense fallback={null}>`. So the whole page was
 * the suspended subtree and the server sent nothing:
 *
 *     <body>...<template data-dgst="BAILOUT_TO_CLIENT_SIDE_RENDERING">...
 *
 * Measured against production: those five pages returned a ~12-13KB body
 * with no chrome and no content, while the pages without the bailout (/,
 * /elections, /about) returned 20-326KB of real markup. Nothing at all was
 * painted until ~653KB of JavaScript had downloaded, parsed and hydrated —
 * which is the "page loads but the content fills in a couple of seconds
 * later" this fixes.
 *
 * The frame does not depend on the query string, so it does not belong
 * behind the boundary. Rendering it here gets the navbar, the masthead and
 * a content skeleton into the first paint; only the part that genuinely
 * needs the URL waits for the client.
 *
 * This is deliberately NOT the real content: the data is per-request and
 * these routes are prerendered, so the server has nothing to fill in. It
 * replaces a blank page with the page's shape, which is what the reader
 * actually perceives as "loading".
 */
export default function PageFallback({
  eyebrow,
  title,
  rows = 4,
}: {
  eyebrow: string;
  title: React.ReactNode;
  /** Roughly how many content blocks this page shows, so the skeleton is
   *  about the height of what replaces it and the layout does not jump. */
  rows?: number;
}) {
  return (
    <div className="min-h-screen">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-5xl mx-auto">
          <PageMasthead className="mb-6" eyebrow={eyebrow} title={title} />
          <div aria-busy="true" aria-live="polite" className="space-y-4">
            <span className="sr-only">Loading…</span>
            {Array.from({ length: rows }).map((_, i) => (
              <div
                key={i}
                className="border border-ink-lo/20 p-4 animate-pulse"
                aria-hidden="true"
              >
                <div className="h-4 w-2/3 bg-ink-lo/20 mb-3" />
                <div className="h-3 w-full bg-ink-lo/10 mb-2" />
                <div className="h-3 w-5/6 bg-ink-lo/10" />
              </div>
            ))}
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
