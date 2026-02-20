"use client";

import Link from "next/link";
import VisitorCounter from "@/components/effects/VisitorCounter";

export default function Footer() {
  return (
    <footer className="border-t border-matrix-green/20 bg-crt-black/80 py-8 px-4">
      <div className="max-w-4xl mx-auto flex flex-col items-center gap-6">
        {/* Coming soon teaser */}
        <div className="terminal-window max-w-sm w-full p-4 text-center">
          <div className="text-neon-pink/60 font-pixel text-[10px] mb-1">
            COMING SOON FROM CIVITAS
          </div>
          <div className="text-matrix-green/50 text-sm space-y-1">
            <div>{">"} House Rep Tracker</div>
            <div>{">"} Lobbying Timeline Visualizer</div>
            <div>{">"} Corporate Influence Scorecards</div>
          </div>
        </div>

        {/* Links */}
        <div className="flex flex-wrap justify-center gap-4 text-lg">
          <Link
            href="/senator-scorecard"
            className="text-matrix-green/60 hover:text-matrix-green transition-colors"
          >
            SENATOR SCORECARD
          </Link>
          <span className="text-matrix-green/20">|</span>
          <span className="text-matrix-green/30 cursor-not-allowed">GUESTBOOK (COMING SOON)</span>
        </div>

        {/* Visitor Counter */}
        <VisitorCounter />

        {/* Retro badge */}
        <div className="text-center">
          <div className="inline-block border border-matrix-green/20 px-3 py-1 text-[10px] font-pixel text-matrix-green/30">
            BEST VIEWED IN NETSCAPE NAVIGATOR 4.0
          </div>
        </div>

        {/* Disclaimer */}
        <p className="text-xs text-matrix-green/25 max-w-lg text-center leading-relaxed">
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
