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

const EXTERNAL_LINKS: readonly { href: string; label: string; aria: string }[] = [
  {
    href: "https://bsky.app/profile/civitas-research.org",
    label: "Bluesky",
    aria: "Civitas on Bluesky",
  },
  {
    href: "https://github.com/kamoras/civitas",
    label: "Source",
    aria: "Civitas source code on GitHub",
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

        <div className="grid grid-cols-1 gap-6 pt-5 md:grid-cols-12 md:gap-9">
          <p className="font-mono text-xs leading-[1.9] tracking-[0.08em] text-ink-min md:col-span-4">
            NON-PROFIT PUBLIC-INTEREST PROJECT
            <br />
            NO PARTY, CANDIDATE OR PAC MONEY
            <br />
            AGPL-3.0 · SELF-HOSTED · OPEN DATA
            <br />
            NO ACCOUNTS · NO ADS · NO TRACKERS
          </p>

          <p className="font-display text-sm leading-relaxed text-ink-lo md:col-span-8">
            All data sourced from public records: FEC campaign finance filings (fec.gov),
            OpenSecrets.org donor &amp; industry data, GovTrack.us &amp; MapLight voting records,
            and Senate Lobbying Disclosure Act filings (lda.senate.gov). The Representation
            Scorecard is a weighted composite metric — not a measure of illegality or wrongdoing.
            Correlation between donations and votes does not prove causation. Verify all data at the
            original sources. Draw your own conclusions.
          </p>
        </div>
      </div>
    </footer>
  );
}
