import Link from "next/link";

/**
 * Colophon footer.
 *
 * Same links and the same disclaimer as before — that disclaimer is the
 * project's epistemic position, not decoration, so it is kept verbatim and
 * given a size someone can actually read it at (it was 12px, dimmed, and
 * centred at a 32rem measure).
 *
 * The layout is a printed form's footer: a 3px block rule, a left-aligned
 * register of links, then the standing statement. Centred stacks read
 * brochure; left-aligned registers read record.
 */

const INTERNAL_LINKS: readonly { href: string; label: string }[] = [
  { href: "/politicians", label: "Politicians" },
  { href: "/about", label: "Methodology" },
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
    href: "https://bsky.app/profile/civitas-research.org",
    label: "Bluesky",
    aria: "Civitas on Bluesky (opens in new tab)",
  },
  {
    href: "https://github.com/kamoras/civitas",
    label: "Source",
    aria: "Civitas source code on GitHub (opens in new tab)",
  },
];

const linkClass =
  "font-mono text-xs uppercase tracking-[0.11em] text-ink-lo hover:text-ink-hi transition-colors";

export default function Footer() {
  return (
    <footer className="border-t-3 border-white/15 bg-surface-base px-4 py-8 sm:px-6">
      <div className="mx-auto max-w-7xl">
        <nav
          aria-label="Footer navigation"
          className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-white/[0.07] pb-5"
        >
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
        <div className="grid grid-cols-1 gap-6 pt-5 md:grid-cols-12 md:gap-9">
          <div className="md:col-span-4">
            <p className="font-mono text-xs leading-[1.9] tracking-[0.08em] text-ink-min">
              NON-PROFIT PUBLIC-INTEREST PROJECT
              <br />
              NO PARTY, CANDIDATE OR PAC MONEY
              <br />
              AGPL-3.0 · SELF-HOSTED · OPEN DATA
              <br />
              NO ACCOUNTS · NO ADS · NO TRACKERS
            </p>
          </div>

          <div className="md:col-span-8">
            <p className="font-display text-base leading-relaxed text-ink-lo">
              All data sourced from public records: FEC campaign finance filings (fec.gov),
              OpenSecrets.org donor &amp; industry data, GovTrack.us &amp; MapLight voting records,
              and Senate Lobbying Disclosure Act filings (lda.senate.gov). The Representation
              Scorecard is a weighted composite metric — not a measure of illegality or wrongdoing.
              Correlation between donations and votes does not prove causation. Verify all data at
              the original sources. Draw your own conclusions.
            </p>
          </div>
        </div>
      </div>
    </footer>
  );
}
