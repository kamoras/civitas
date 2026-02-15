"use client";

import { useEffect, useRef, useState } from "react";
import GlitchText from "@/components/effects/GlitchText";

interface StatCardProps {
  value: string;
  label: string;
  prefix?: string;
  source?: string;
}

function StatCard({ value, label, prefix = "", source }: StatCardProps) {
  const [visible, setVisible] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setVisible(true);
      },
      { threshold: 0.3 }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`terminal-window p-0 transition-all duration-700 ${
        visible ? "opacity-100 translate-y-0" : "opacity-0 translate-y-8"
      }`}
    >
      <div className="terminal-titlebar">
        <div className="terminal-dot red" />
        <div className="terminal-dot yellow" />
        <div className="terminal-dot green" />
        <span className="text-xs text-matrix-green/40 ml-2 font-mono truncate">
          public_record.dat
        </span>
      </div>
      <div className="p-4 sm:p-6 text-center">
        <div className="text-2xl sm:text-4xl md:text-5xl font-pixel text-neon-cyan neon-cyan mb-3 break-all">
          {prefix}
          {value}
        </div>
        <div className="text-sm sm:text-base text-matrix-green/60 px-2">{label}</div>
        {source && <div className="text-[10px] text-matrix-green/30 mt-2">{source}</div>}
      </div>
    </div>
  );
}

export default function WakeUpSection() {
  return (
    <section
      id="the-numbers"
      className="py-24 px-4 bg-gradient-to-b from-crt-black to-crt-black/80"
    >
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-16">
          <GlitchText
            text="THE NUMBERS DON'T LIE"
            as="h2"
            className="font-pixel text-sm sm:text-xl md:text-2xl text-neon-pink"
          />
          <p className="text-matrix-green/40 text-sm mt-3">
            All figures from public federal filings and nonpartisan research organizations.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <StatCard
            value="14.4B"
            prefix="$"
            label="Total corporate lobbying spend in 2023"
            source="Source: OpenSecrets.org"
          />
          <StatCard
            value="89%"
            label="Of senators vote with their biggest donors most of the time"
            source="Source: MapLight / FEC cross-reference"
          />
          <StatCard
            value="27,432"
            prefix="$"
            label="Average lobbying spend per congressional vote cast"
            source="Source: Senate LDA filings"
          />
        </div>

        <div className="ascii-divider mt-16">{"=".repeat(60)}</div>
      </div>
    </section>
  );
}
