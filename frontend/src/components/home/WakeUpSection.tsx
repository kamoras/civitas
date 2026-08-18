"use client";

import { useEffect, useRef, useState } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";

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
      <TerminalTitlebar title="Public record" />
      <div className="p-4 sm:p-6 text-center">
        <div className="text-3xl sm:text-5xl md:text-6xl font-mono text-signal-cyan mb-3 tracking-wide">
          {prefix}
          {value}
        </div>
        <div className="text-xs font-mono text-ink-lo px-2 leading-relaxed tracking-wide">
          {label}
        </div>
        {source && <div className="text-xs font-mono text-ink-min mt-2">{source}</div>}
      </div>
    </div>
  );
}

export default function WakeUpSection() {
  return (
    <section
      id="the-numbers"
      className="py-24 px-4 bg-gradient-to-b from-surface-base to-surface-base/80"
    >
      <div className="max-w-5xl mx-auto">
        <div className="mb-16 border-b-3 border-phos pb-5">
          <h2 className="font-mono text-xs tracking-[0.35em] uppercase text-ink-lo mb-2">
            WHAT WE TRACK
          </h2>
          <p className="text-ink-min text-xs font-mono tracking-wider mt-2">
            Senate. House of Representatives. Presidents. Supreme Court. Public data only.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-6">
          <StatCard
            value="100"
            label="Senators scored across 5 representation metrics using FEC, Congress.gov, and GovInfo data"
            source="Source: FEC + Congress.gov + GovInfo APIs"
          />
          <StatCard
            value="435"
            label="House representatives scored with the same transparency framework applied to the Senate"
            source="Source: FEC + Congress.gov + GovInfo APIs"
          />
          <StatCard
            value="47"
            label="Presidents ranked from Washington to today, with live data for modern administrations"
            source="Source: Federal Register + BLS + C-SPAN Historians Survey"
          />
          <StatCard
            value="9"
            label="Supreme Court justices scored on impartiality and ideological consistency from case data"
            source="Source: Oyez Project + supremecourt.gov"
          />
        </div>

        <div className="ascii-divider mt-16" aria-hidden="true" role="presentation">
          {"=".repeat(60)}
        </div>
      </div>
    </section>
  );
}
