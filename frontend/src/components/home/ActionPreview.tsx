"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import { fetchActionIssues } from "@/lib/api";
import type { ActionIssue } from "@/types/action";

const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

export default function ActionPreview() {
  const [issues, setIssues] = useState<ActionIssue[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetchActionIssues()
      .then((data) => setIssues(data.issues || []))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded) {
    return (
      <div role="status" aria-live="polite" className="sr-only">
        Loading action issues...
      </div>
    );
  }

  if (issues.length === 0) return null;

  const hero = issues[0];
  const others = issues.slice(1, 4);

  return (
    <section className="py-16 sm:py-24 px-4 bg-gradient-to-b from-crt-black to-crt-black/80">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-10">
          <GlitchText
            text="TODAY'S TOP ISSUES"
            as="h2"
            className="font-pixel text-sm sm:text-xl md:text-2xl text-neon-cyan"
          />
          <p className="text-matrix-green/40 text-sm mt-3">
            Cross-referenced from news and social media trends. Updated hourly.
          </p>
        </div>

        <div className="terminal-window border-t-2 border-t-neon-cyan/50 p-5 sm:p-8 mb-6">
          <div className="flex items-center gap-3 mb-4">
            <span className="font-pixel text-[10px] text-neon-cyan/60 bg-neon-cyan/10 border border-neon-cyan/30 px-2 py-0.5">
              #1 ISSUE
            </span>
            {hero.policyAreas.map((area) => (
              <span
                key={area}
                className="text-[10px] px-1.5 py-0.5 border border-neon-yellow/30 text-neon-yellow/70 font-pixel"
              >
                {area}
              </span>
            ))}
          </div>

          <h3 className="font-pixel text-base sm:text-xl text-matrix-green mb-3 leading-relaxed">
            {hero.title}
          </h3>

          <p className="text-matrix-green/70 text-sm leading-relaxed mb-5">
            {hero.summary}
          </p>

          {hero.relatedSenators && hero.relatedSenators.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-5">
              {hero.relatedSenators.map((s) => (
                <Link
                  key={s.id}
                  href={`/scorecard?branch=senate&state=${s.state}&senator=${s.id}`}
                  className="flex items-center gap-2 px-3 py-1.5 border border-matrix-green/20 bg-matrix-dark-green/20 hover:border-neon-cyan/40 transition-colors group"
                >
                  <span className={`font-pixel text-[10px] ${PARTY_COLORS[s.party]}`}>
                    [{s.party}]
                  </span>
                  <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green">
                    {s.name}
                  </span>
                  <span className="text-[10px] text-matrix-green/40">
                    {s.state}
                  </span>
                  <span className="text-[10px] font-pixel text-neon-cyan/60">
                    {Math.round(s.overallScore)}/100
                  </span>
                </Link>
              ))}
            </div>
          )}

          <Link
            href="/action"
            className="inline-block text-sm text-neon-cyan/70 hover:text-neon-cyan transition-colors font-pixel"
          >
            {">"} SEE FULL DETAILS + ACTIONS →
          </Link>
        </div>

        {others.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
            {others.map((issue) => (
              <Link
                key={issue.id}
                href="/action"
                className="terminal-window p-4 hover:border-matrix-green/40 transition-colors group"
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="font-pixel text-[10px] text-matrix-green/40">
                    #{issue.rank}
                  </span>
                  {issue.policyAreas.slice(0, 1).map((area) => (
                    <span
                      key={area}
                      className="text-[10px] px-1 py-0.5 border border-neon-yellow/20 text-neon-yellow/50 font-pixel"
                    >
                      {area}
                    </span>
                  ))}
                </div>
                <h4 className="font-pixel text-xs text-matrix-green/80 group-hover:text-matrix-green leading-relaxed">
                  {issue.title}
                </h4>
              </Link>
            ))}
          </div>
        )}

        <div className="text-center">
          <Link href="/action" className="btn-retro">
            [ OPEN ACTION CENTER ]
          </Link>
        </div>
      </div>
    </section>
  );
}
