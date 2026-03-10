"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import GlitchText from "@/components/effects/GlitchText";
import { fetchActionIssues, fetchMonitors } from "@/lib/api";
import type { NationalMonitor } from "@/lib/api";
import type { ActionIssue, DailyTheme } from "@/types/action";

const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

export default function ActionPreview() {
  const [issues, setIssues] = useState<ActionIssue[]>([]);
  const [monitors, setMonitors] = useState<NationalMonitor[]>([]);
  const [theme, setTheme] = useState<DailyTheme | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    Promise.all([
      fetchActionIssues().then((data) => {
        setIssues(data.issues || []);
        setTheme(data.theme || null);
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
          <GlitchText
            text="TODAY'S TOP ISSUES"
            as="h2"
            className="font-pixel text-sm sm:text-xl md:text-2xl text-neon-cyan"
          />
          <p className="text-matrix-green/40 text-sm mt-3">
            Cross-referenced from news and social media trends. Updated hourly.
          </p>
        </div>

        {hero && (
          <>
            {theme && <HeroThemeStyle theme={theme} />}
            <div className={theme ? "hero-preview-panel terminal-window border p-5 sm:p-8 mb-6" : "terminal-window border-t-2 border-t-neon-cyan/50 p-5 sm:p-8 mb-6"}>
              {theme && (
                <>
                  <div className="hero-corner hero-corner--tl" aria-hidden="true" />
                  <div className="hero-corner hero-corner--tr" aria-hidden="true" />
                  <div className="hero-corner hero-corner--bl" aria-hidden="true" />
                  <div className="hero-corner hero-corner--br" aria-hidden="true" />
                  <div className="hero-urgency-bar mb-4" aria-hidden="true" />
                </>
              )}

              <div className="flex items-center gap-3 mb-4 flex-wrap">
                <span className={`font-pixel text-[10px] px-2 py-0.5 border ${theme ? "hero-tag" : "text-neon-cyan/60 bg-neon-cyan/10 border-neon-cyan/30"}`}>
                  #1 ISSUE
                </span>
                {theme && (
                  <span className="hero-mood-badge font-pixel">
                    <span className="hero-mood-dot" />
                    {theme.mood}
                  </span>
                )}
                {hero.policyAreas.map((area) => (
                  <span
                    key={area}
                    className="text-[10px] px-1.5 py-0.5 border border-neon-yellow/30 text-neon-yellow/70 font-pixel"
                  >
                    {area}
                  </span>
                ))}
              </div>

              <h3 className={`font-pixel text-base sm:text-xl mb-3 leading-relaxed ${theme ? "hero-accent-text" : "text-matrix-green"}`}>
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
          </>
        )}

        {activeMonitors.length > 0 && (
          <div className="mb-8">
            <h3 className="font-pixel text-[10px] text-amber-400/60 tracking-widest text-center mb-4">
              NATIONAL MONITORS — ONGOING CONCERNS WE ARE TRACKING
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
                    <span className="font-pixel text-[10px] text-amber-400/50 uppercase truncate">
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
            [ OPEN ACTION CENTER ]
          </Link>
        </div>
      </div>
    </section>
  );
}

function HeroThemeStyle({ theme }: { theme: DailyTheme }) {
  const glow = theme.glowIntensity;
  const speed = theme.animationSpeed;
  const alt = theme.accentAlt || theme.accent;

  const css = `
    @keyframes hero-border-glow {
      0%, 100% { box-shadow: 0 0 ${8 * glow}px ${theme.accent}20, inset 0 0 ${12 * glow}px ${theme.accent}08; border-color: ${theme.accent}30; }
      50% { box-shadow: 0 0 ${20 * glow}px ${theme.accent}35, inset 0 0 ${25 * glow}px ${theme.accent}12; border-color: ${theme.accent}60; }
    }
    @keyframes hero-accent-pulse {
      0%, 100% { opacity: 0.5; }
      50% { opacity: 1; }
    }
    @keyframes hero-urgency-crawl {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(100%); }
    }
    @keyframes hero-corner-flash {
      0%, 70%, 100% { opacity: 0.3; }
      80% { opacity: 0.8; }
    }

    .hero-preview-panel {
      position: relative;
      overflow: hidden;
      border-left: 3px solid ${theme.accent}80;
      border-color: ${theme.accent}40;
      background:
        linear-gradient(180deg, ${theme.heroGradient?.[0] || "#0a0a0f"} 0%, ${theme.heroGradient?.[1] || "#0d1117"} 50%, ${theme.heroGradient?.[2] || "#0a0f0a"} 100%);
      animation: hero-border-glow ${speed}s ease-in-out infinite;
    }
    .hero-preview-panel > * { position: relative; z-index: 2; }

    .hero-corner { position: absolute; width: 16px; height: 16px; pointer-events: none; z-index: 3; }
    .hero-corner--tl { top: 0; left: 0; border-top: 2px solid ${theme.accent}60; border-left: 2px solid ${theme.accent}60; animation: hero-corner-flash ${speed * 2}s ease-in-out infinite; }
    .hero-corner--tr { top: 0; right: 0; border-top: 2px solid ${theme.accent}60; border-right: 2px solid ${theme.accent}60; animation: hero-corner-flash ${speed * 2}s ease-in-out infinite ${speed * 0.5}s; }
    .hero-corner--bl { bottom: 0; left: 0; border-bottom: 2px solid ${alt}60; border-left: 2px solid ${alt}60; animation: hero-corner-flash ${speed * 2}s ease-in-out infinite ${speed}s; }
    .hero-corner--br { bottom: 0; right: 0; border-bottom: 2px solid ${alt}60; border-right: 2px solid ${alt}60; animation: hero-corner-flash ${speed * 2}s ease-in-out infinite ${speed * 1.5}s; }

    .hero-urgency-bar {
      position: relative;
      height: 2px;
      background: ${theme.accent}15;
      overflow: hidden;
    }
    .hero-urgency-bar::after {
      content: '';
      position: absolute;
      inset: 0;
      width: 40%;
      background: linear-gradient(90deg, transparent, ${theme.accent}80, ${alt}80, transparent);
      animation: hero-urgency-crawl ${speed * 1.5}s linear infinite;
    }

    .hero-tag {
      color: ${theme.accent}cc;
      background: ${theme.accent}15;
      border-color: ${theme.accent}40;
    }

    .hero-accent-text {
      color: ${theme.accent};
      text-shadow: 0 0 ${8 * glow}px ${theme.accent}40;
    }

    .hero-mood-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 10px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: ${theme.accent}aa;
      border: 1px solid ${theme.accent}25;
      background: ${theme.accent}08;
      padding: 2px 8px;
    }
    .hero-mood-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: ${theme.accent};
      animation: hero-accent-pulse ${speed}s ease-in-out infinite;
    }

    @media (prefers-reduced-motion: reduce) {
      .hero-preview-panel, .hero-preview-panel::before, .hero-preview-panel::after,
      .hero-urgency-bar::after, .hero-corner, .hero-mood-dot { animation: none !important; }
    }
  `;

  return <style dangerouslySetInnerHTML={{ __html: css }} />;
}
