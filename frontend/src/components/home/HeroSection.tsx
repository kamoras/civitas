"use client";

import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import TypewriterText from "@/components/effects/TypewriterText";
import Marquee from "@/components/effects/Marquee";

const MARQUEE_ITEMS = [
  "Action Center: today's top policy issues with national monitors tracking ongoing concerns",
  "535 members of Congress scored: 100 senators + 435 House representatives",
  "All data sourced from public federal records: FEC, Congress.gov, GovInfo, Federal Register, BLS",
  "No accounts, no tracking, no ads -- your visit leaves no trace and no data leaves this server",
  "National monitors auto-detect recurring issues and build a year-in-review timeline",
  "Nightly pipeline processes campaign finance, voting records, and Congressional Record transcripts",
];

export default function HeroSection() {
  return (
    <section className="relative min-h-screen flex flex-col justify-center items-center text-center px-4">
      <div className="mb-8">
        <GlitchText
          text="CIVITAS"
          as="h1"
          className="font-pixel text-3xl sm:text-5xl md:text-7xl text-matrix-green animate-pulse-neon"
        />
      </div>

      <div className="mb-12 text-xl sm:text-2xl md:text-3xl text-matrix-green/80 max-w-3xl">
        <TypewriterText
          text="> CONGRESSIONAL DATA. SCORED. SOURCED. SEARCHABLE."
          speed={40}
          startDelay={800}
        />
      </div>

      <div className="flex flex-col sm:flex-row gap-4 mb-16">
        <Link href="/action" className="btn-retro">
          [ ACTION CENTER ]
        </Link>
        <Link href="/scorecard" className="btn-retro btn-retro-pink">
          [ SCORECARDS ]
        </Link>
      </div>

      <div className="absolute bottom-0 left-0 right-0">
        <Marquee items={MARQUEE_ITEMS} />
      </div>
    </section>
  );
}
