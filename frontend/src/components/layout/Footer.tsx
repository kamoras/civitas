import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-matrix-green/20 bg-crt-black/80 py-8 px-4">
      <div className="max-w-4xl mx-auto flex flex-col items-center gap-6">
        {/* Links */}
        <nav aria-label="Footer navigation" className="flex flex-wrap justify-center gap-6">
          <Link
            href="/scorecard"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            SCORECARD
          </Link>
          <Link
            href="/about"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            METHODOLOGY
          </Link>
          <Link
            href="/changelog"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            CHANGELOG
          </Link>
          <Link
            href="/accessibility"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            ACCESSIBILITY
          </Link>
          <Link
            href="/environmental"
            className="text-matrix-green/40 hover:text-matrix-green/80 transition-colors font-mono text-xs tracking-widest uppercase"
          >
            ENVIRONMENTAL
          </Link>
          <a
            href="https://bsky.app/profile/civitas-research.bsky.social"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Civitas on Bluesky (opens in new tab)"
            className="text-neon-cyan/50 hover:text-neon-cyan transition-colors font-mono text-xs tracking-widest uppercase"
          >
            🦋 BLUESKY
          </a>
        </nav>

        {/* Disclaimer */}
        <p className="text-xs text-matrix-green/50 max-w-lg text-center leading-relaxed">
          All data sourced from public records: FEC campaign finance filings (fec.gov),
          OpenSecrets.org donor &amp; industry data, GovTrack.us &amp; MapLight voting records, and
          Senate Lobbying Disclosure Act filings (lda.senate.gov). The Representation Scorecard is
          a weighted composite metric — not a measure of illegality or wrongdoing. Correlation
          between donations and votes does not prove causation. Verify all data at the original
          sources. Draw your own conclusions.
        </p>
      </div>
    </footer>
  );
}
