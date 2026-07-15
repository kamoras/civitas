"use client";

import Link from "next/link";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import TypewriterText from "@/components/effects/TypewriterText";
import { useEffect, useRef, useState } from "react";

const MANIFESTO_LINES = [
  "> cat about.txt",
  "",
  "CIVITAS is not a news site.",
  "We're not journalists. We're not pundits.",
  "",
  "We are citizens who got tired of digging",
  "through FEC filings and Senate records",
  "just to see who's funding who.",
  "",
  "So we built this. Campaign finance, votes,",
  "lobbying, and legislation — all in one place.",
  "",
  "Every dollar is on the public record.",
  "We just made it easier to find.",
];

const TYPEWRITER_SPEED = 20;
const INTER_LINE_GAP = 80;

// Each line starts only after the previous line finishes typing, ensuring
// exactly one blinking cursor is visible at a time.
function computeLineDelays(lines: string[], initialDelay: number): number[] {
  let cum = initialDelay;
  return lines.map((line) => {
    const d = cum;
    if (line !== "") cum += line.length * TYPEWRITER_SPEED + INTER_LINE_GAP;
    return d;
  });
}

const LINE_DELAYS = computeLineDelays(MANIFESTO_LINES, 500);

export default function ManifestoSection() {
  const [visible, setVisible] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setVisible(true);
      },
      { threshold: 0.2 }
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const fullText = MANIFESTO_LINES.filter(Boolean).slice(1).join(" ");

  return (
    <section className="py-24 px-4" ref={ref} aria-label={fullText}>
      <div className="max-w-3xl mx-auto">
        <div className="terminal-window">
          <TerminalTitlebar title="about.txt" />
          <div className="p-6 sm:p-8 min-h-[300px]" aria-hidden="true">
            {visible ? (
              <div className="space-y-1">
                {MANIFESTO_LINES.map((line, i) => (
                  <div key={i} className="text-base sm:text-lg">
                    {line === "" ? (
                      <br />
                    ) : i === 0 ? (
                      <span className="text-neon-cyan">{line}</span>
                    ) : (
                      <TypewriterText
                        text={line}
                        speed={TYPEWRITER_SPEED}
                        startDelay={LINE_DELAYS[i]}
                        className="text-matrix-green/90"
                      />
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-matrix-green/30 animate-blink text-xl">_ </div>
            )}
          </div>
        </div>

        <div className="text-center mt-12">
          <Link href="/politicians" className="btn-retro text-lg">
            CHECK YOUR REPRESENTATIVES
          </Link>
          <p className="mt-4 text-sm text-matrix-green/40">no login. no paywall. just the data.</p>
        </div>
      </div>
    </section>
  );
}
