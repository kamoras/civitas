"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchActionIssues, fetchMonitors } from "@/lib/api";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue } from "@/types/action";
import { PARTY_COLORS } from "@/lib/partyStyles";

export default function ActionPreview() {
  const [issues, setIssues] = useState<ActionIssue[]>([]);
  const [monitors, setMonitors] = useState<NationalMonitor[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    Promise.all([
      fetchActionIssues().then((data) => {
        setIssues(data.issues || []);
      }).catch(() => {}),
      fetchMonitors().then((data) => setMonitors(data.monitors || [])).catch(() => {}),
    ]).finally(() => setLoaded(true));
  }, []);

  if (!loaded) {
    return (
      <div role="status" aria-live="polite" className="sr-only">
        Loading action issues...
      </div>
    );
  }

  if (issues.length === 0 && monitors.length === 0) return null;

  const hero = issues[0];
  const others = issues.slice(1, 4);
  const activeMonitors = monitors.filter((m) => m.status === "active").slice(0, 6);

  return (
    <section className="py-16 sm:py-24 px-4 bg-gradient-to-b from-crt-black to-crt-black/80">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-10">
          <h2 className="font-mono text-xs tracking-[0.35em] uppercase text-neon-cyan/80 mb-2">
            TODAY&apos;S TOP ISSUES
          </h2>
          <p className="text-matrix-green/35 text-xs font-mono tracking-wider mt-2">
            Cross-referenced from news and social media trends. Updated hourly.
          </p>
        </div>

        {hero && (
          <>
            <div className="terminal-window border-t-2 border-t-neon-cyan/50 p-5 sm:p-8 mb-6">
              <div className="flex items-center gap-3 mb-4 flex-wrap">
                <span className="font-mono text-[10px] tracking-widest px-2 py-0.5 border text-neon-cyan/60 bg-neon-cyan/10 border-neon-cyan/30">
                  #1 ISSUE
                </span>
                {hero.policyAreas.map((area) => (
                  <span
                    key={area}
                    className="text-[10px] px-1.5 py-0.5 border border-neon-yellow/30 text-neon-yellow/70 font-mono tracking-wide"
                  >
                    {area}
                  </span>
                ))}
              </div>

              <h3 className="font-pixel text-base sm:text-xl mb-3 leading-relaxed text-matrix-green">
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
                      href={`/politicians/${s.id}`}
                      className="flex items-center gap-2 px-3 py-1.5 border border-matrix-green/20 bg-matrix-dark-green/20 hover:border-neon-cyan/40 transition-colors group"
                    >
                      <span className={`font-mono text-[10px] ${PARTY_COLORS[s.party]}`}>
                        [{s.party}]
                      </span>
                      <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green">
                        {s.name}
                      </span>
                      <span className="text-[10px] text-matrix-green/40">
                        {s.state}
                      </span>
                      <span className="text-[10px] font-mono text-neon-cyan/60">
                        {Math.round(s.overallScore)}/100
                      </span>
                    </Link>
                  ))}
                </div>
              )}

              <Link
                href="/action"
                className="inline-block font-mono text-xs tracking-widest text-neon-cyan/60 hover:text-neon-cyan transition-colors"
              >
                SEE ALL ISSUES →
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
                      <span className="font-mono text-[10px] text-matrix-green/40">
                        #{issue.rank}
                      </span>
                      {issue.policyAreas.slice(0, 1).map((area) => (
                        <span
                          key={area}
                          className="text-[10px] px-1 py-0.5 border border-neon-yellow/20 text-neon-yellow/50 font-mono tracking-wide"
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
          </>
        )}

        {activeMonitors.length > 0 && (
          <div className="mb-8">
            <h3 className="font-mono text-[10px] tracking-widest text-amber-400/50 text-center mb-4 uppercase">
              National Monitors — Ongoing Concerns We Are Tracking
            </h3>
            <div className={`grid gap-3 ${
              activeMonitors.length <= 2
                ? "grid-cols-1 sm:grid-cols-2 max-w-3xl mx-auto"
                : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
            }`}>
              {activeMonitors.map((m) => (
                <Link
                  key={m.slug}
                  href="/action?tab=monitors"
                  className="terminal-window p-5 hover:border-amber-400/30 transition-colors group"
                >
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      className="w-2 h-2 rounded-full bg-green-400 shrink-0"
                      aria-hidden="true"
                    />
                    <span className="font-mono text-[9px] tracking-wider text-amber-400/45 uppercase truncate">
                      {m.category}
                    </span>
                  </div>
                  <h4 className="font-pixel text-xs sm:text-sm text-matrix-green/80 group-hover:text-matrix-green leading-relaxed mb-2">
                    {m.title}
                  </h4>
                  <p className="text-matrix-green/40 text-xs leading-relaxed mb-3 line-clamp-2">
                    {m.description}
                  </p>
                  <div className="flex items-center gap-3 text-[10px] text-matrix-green/40">
                    <span>{m.updateCount} update{m.updateCount !== 1 ? "s" : ""}</span>
                    <span>since {m.createdAt}</span>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}

        <div className="text-center">
          <Link href="/action" className="btn-retro">
            OPEN ACTION CENTER
          </Link>
        </div>
      </div>
    </section>
  );
}
