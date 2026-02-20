"use client";

import Link from "next/link";
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
  "So we built this. Public data,",
  "organized so you can actually read it.",
  "",
  "Every dollar is on the public record.",
  "We just made it easier to find.",
  "",
  "This is the first tool. More are coming.",
];

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

  return (
    <section className="py-24 px-4" ref={ref}>
      <div className="max-w-3xl mx-auto">
        <div className="terminal-window">
          <div className="terminal-titlebar">
            <div className="terminal-dot red" />
            <div className="terminal-dot yellow" />
            <div className="terminal-dot green" />
            <span className="text-xs text-matrix-green/40 ml-2 font-mono">about.txt</span>
          </div>
          <div className="p-6 sm:p-8 min-h-[300px]">
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
                        speed={25}
                        startDelay={500 + i * 400}
                        className="text-matrix-green/90"
                      />
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-matrix-green/30 animate-blink text-xl">{">"} _</div>
            )}
          </div>
        </div>

        <div className="text-center mt-12">
          <Link href="/senator-scorecard" className="btn-retro text-lg">
            [ CHECK YOUR SENATORS ]
          </Link>
          <p className="mt-4 text-sm text-matrix-green/40">no login. no paywall. just the data.</p>
        </div>
      </div>
    </section>
  );
}
