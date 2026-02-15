"use client";

import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import TypewriterText from "@/components/effects/TypewriterText";
import Marquee from "@/components/effects/Marquee";

const MARQUEE_ITEMS = [
  "$14.4 BILLION spent on lobbying in 2023 (OpenSecrets.org)",
  "Average senator received $4.2M from corporate PACs last cycle (FEC data)",
  "67% of legislation matches lobbyist proposals (Public Citizen report)",
  "All data on this site is sourced from public federal records",
  "Big Pharma spent $374M lobbying Congress in 2023 (Senate LDA filings)",
  "Top 10 corporate donors contributed $1.2B to Congress last cycle (FEC)",
];

export default function HeroSection() {
  return (
    <section className="relative min-h-screen flex flex-col justify-center items-center text-center px-4">
      <div className="mb-8">
        <GlitchText
          text="MODERN PUNK"
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
        <Link href="/influence-tracker" className="btn-retro">
          [ CHECK YOUR SENATOR ]
        </Link>
        <a href="#the-numbers" className="btn-retro btn-retro-pink">
          [ SEE THE NUMBERS ]
        </a>
      </div>

      <div className="absolute bottom-0 left-0 right-0">
        <Marquee items={MARQUEE_ITEMS} />
      </div>
    </section>
  );
}
