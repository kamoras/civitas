"use client";

import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import TypewriterText from "@/components/effects/TypewriterText";
import Marquee from "@/components/effects/Marquee";

const MARQUEE_ITEMS = [
  "100 senators scored on funding independence, promise persistence, voting record, transparency, and accessibility",
  "47 presidents ranked from Washington to today using historian surveys and live economic data",
  "All data sourced from public federal records: FEC, Congress.gov, GovInfo, Federal Register, BLS",
  "AI analysis runs locally on a Raspberry Pi 5 -- no cloud APIs, no data leaves the device",
  "Search Senate floor speeches, House proceedings, executive orders, and proclamations on the Explore page",
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
          text="> PUBLIC DATA. ZERO SPIN. LOOK IT UP YOURSELF."
          speed={40}
          startDelay={800}
        />
      </div>

      <div className="flex flex-col sm:flex-row gap-4 mb-16">
        <Link href="/scorecard" className="btn-retro">
          [ CHECK YOUR REPRESENTATIVES ]
        </Link>
        <Link href="/explore" className="btn-retro btn-retro-pink">
          [ EXPLORE ISSUES ]
        </Link>
      </div>

      <div className="absolute bottom-0 left-0 right-0">
        <Marquee items={MARQUEE_ITEMS} />
      </div>
    </section>
  );
}
