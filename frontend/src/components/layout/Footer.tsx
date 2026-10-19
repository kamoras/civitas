import Link from "next/link";
import { BSKY_PROFILE_URL, GITHUB_REPO_URL, SITE_TAGLINE } from "@/lib/site";
import ThemeToggle from "@/components/layout/ThemeToggle";

/**
 * Colophon footer.
 *
 * Same links and the same disclaimer as before — that disclaimer is the
 * project's epistemic position, not decoration, so it is kept verbatim and
 * given a size someone can actually read it at (it was 12px, dimmed, and
 * centred at a 32rem measure).
 *
 * The layout is a printed form's footer: a 3px block rule, the site's own
 * pages, then the standing statement. Centred stacks read brochure;
 * left-aligned registers read record.
 *
 * No register of the sections' pages (2026-10): it repeated the header's
 * menu at the foot of every page (on a phone 1,359 px of it at 390 px).
 * The header carries every section's pages at every width, and its desktop
 * panels are in the server HTML while closed (`hidden`), so the links stay
 * crawlable without a second copy here. The pages below are the ones no
 * section lists (routes.test.ts, NOT_IN_A_SECTION).
 */

const INTERNAL_LINKS: readonly { href: string; label: string }[] = [
  { href: "/feeds", label: "Feeds" },
  { href: "/developers", label: "API" },
  { href: "/changelog", label: "Changelog" },
  { href: "/accessibility", label: "Accessibility" },
  { href: "/environmental", label: "Environmental" },
  { href: "/feedback", label: "Feedback" },
];

/* The "(opens in new tab)" suffix is load-bearing, not boilerplate: globals.css
   appends it via `a[target="_blank"]::after` for links that have no aria-label,
   but an aria-label WINS the accessible-name computation and suppresses that
   fallback entirely. Dropping it from these two would have silently removed the
   announcement that every other external link on the site still makes. */
const EXTERNAL_LINKS: readonly { href: string; label: string; aria: string }[] = [
  {
    href: BSKY_PROFILE_URL,
    label: "Bluesky",
    aria: "Civitas on Bluesky (opens in new tab)",
  },
  {
    href: GITHUB_REPO_URL,
    label: "Source",
    aria: "Civitas source code on GitHub (opens in new tab)",
  },
];

// 24 px tall: WCAG 2.5.8's minimum target, in a list of links set close.
const linkClass =
  "inline-flex min-h-6 items-center font-mono text-xs uppercase tracking-[0.11em] text-ink-lo hover:text-ink-hi transition-colors";

export default function Footer() {
  return (
    <footer className="border-t-3 border-white/15 bg-surface-base px-4 py-8 sm:px-6">
      <div className="mx-auto max-w-7xl">
        <nav aria-label="Footer navigation" className="border-b border-white/[0.07] pb-5">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
            {INTERNAL_LINKS.map(({ href, label }) => (
              <Link key={href} href={href} className={linkClass}>
                {label}
              </Link>
            ))}
            {EXTERNAL_LINKS.map(({ href, label, aria }) => (
              <a
                key={href}
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={aria}
                className={linkClass}
              >
                {label}
              </a>
            ))}
            <ThemeToggle />
          </div>
        </nav>

        {/*
          Each column wraps its paragraph in a div rather than placing two <p>
          as adjacent siblings. globals.css sets `p + p { margin-top: 1em }`,
          and that rule relies on adjacent-sibling margin COLLAPSING in normal
          flow — which does not happen in a grid, where it is additive. As bare
          siblings these two would sit 16px out of alignment on md: and open a
          40px gap when they stack on mobile. The globals.css comment calls out
          this exact hazard.
        */}
        <div className="grid grid-cols-1 gap-6 pt-5 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:gap-9">
          <div>
            {/* The colophon: the name and its line, as the header and the
                homepage's nameplate set them, then the terms. */}
            <p className="font-mono text-xs leading-[1.9] tracking-[0.08em] text-ink-min [overflow-wrap:anywhere]">
              <span className="text-ink-hi">CIVITAS RESEARCH</span>
              <br />
              {SITE_TAGLINE.toUpperCase()}
              <br />
              OPEN SOURCE · RUN BY VOLUNTEERS
              <br />
              NO PARTY, CANDIDATE OR PAC MONEY
              <br />
              AGPL-3.0 · SELF-HOSTED · OPEN DATA
              <br />
              NO ACCOUNTS · NO ADS · NO TRACKERS
            </p>
          </div>

          <div>
            <p className="font-sans text-base leading-relaxed text-ink-lo">
              All data sourced from public records, principally: FEC campaign finance filings
              (fec.gov); bills and roll-call votes from Congress.gov, Senate.gov and the House
              Clerk; ideal points from Voteview; Lobbying Disclosure Act filings (lda.gov); the
              Federal Register and GovInfo; and state election offices. Donor industries are
              classified by Civitas Research from FEC filings, not taken from a third party. The
              Representation Scorecard is a weighted composite metric, not a measure of illegality
              or wrongdoing. Correlation between donations and votes does not prove causation.
              Verify all data at the original sources. Draw your own conclusions.
            </p>
          </div>
        </div>
      </div>
    </footer>
  );
}
