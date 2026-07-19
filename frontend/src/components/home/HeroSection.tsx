"use client";

import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import TypewriterText from "@/components/effects/TypewriterText";
import Marquee from "@/components/effects/Marquee";

const MARQUEE_ITEMS = [
  "Action Center: today's top policy issues with national monitors tracking ongoing concerns",
  "535 members of Congress scored: 100 senators + 435 House representatives",
  "All data sourced from public federal records: FEC, Congress.gov, GovInfo, Federal Register, BLS",
  "No accounts, no cookies, no ads, no third-party trackers -- only anonymized, self-hosted visit counts, never shared or sold",
  "National monitors auto-detect recurring issues and build a year-in-review timeline",
  "Nightly pipeline processes campaign finance, voting records, and Congressional Record transcripts",
];

export default function HeroSection() {
  return (
    <section className="relative min-h-screen flex flex-col justify-center items-center text-center px-4">
      <div className="mb-6">
        <GlitchText
          text="CIVITAS"
          as="h1"
          className="font-pixel text-3xl sm:text-5xl md:text-7xl text-matrix-green neon-green"
        />
      </div>

      <div className="mb-2 text-matrix-green/30 font-mono text-xs tracking-[0.4em] uppercase">
        public record terminal
      </div>

      <div className="mb-12 text-base sm:text-lg md:text-xl text-matrix-green/70 max-w-2xl font-mono">
        <TypewriterText
          text="CONGRESSIONAL DATA. SCORED. SOURCED. SEARCHABLE."
          speed={35}
          startDelay={600}
        />
      </div>

      <div className="flex flex-col sm:flex-row gap-3 mb-4">
        <Link href="/action" className="btn-retro">
          ACTION CENTER
        </Link>
        <Link href="/politicians" className="btn-retro btn-retro-pink">
          POLITICIANS
        </Link>
      </div>

      <p className="mb-12 text-[11px] font-mono text-matrix-green/30 tracking-widest uppercase">
        No login · no paywall · just the data
      </p>

      <div className="absolute bottom-0 left-0 right-0">
        <Marquee items={MARQUEE_ITEMS} />
      </div>
    </section>
  );
}
