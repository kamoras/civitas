"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import GlitchText from "@/components/effects/GlitchText";
import { fetchActionIssues, fetchRecentByBranch, fetchMonitors, fetchMonitorDetail } from "@/lib/api";
import type { NationalMonitor, NationalMonitorDetail } from "@/lib/api";
import type { ActionIssue, ActionIssuesResponse, ActionItem, DailyTheme } from "@/types/action";
import type { BranchDocument } from "@/lib/api";
import { STATES } from "@/data/states";

const GlobeTab = dynamic(() => import("@/components/action/GlobeTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-cyan animate-pulse font-pixel text-sm">LOADING GLOBE...</div>
    </div>
  ),
});

const ElectionsTab = dynamic(() => import("@/components/action/ElectionsTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-yellow animate-pulse font-pixel text-sm">LOADING ELECTIONS...</div>
    </div>
  ),
});

const PARTY_COLORS: Record<string, string> = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

const PARTY_BORDER: Record<string, string> = {
  D: "border-dem-blue/30",
  R: "border-rep-red/30",
  I: "border-ind-purple/30",
};

type Tab = "issues" | "monitors" | "elections" | "senate" | "house" | "executive" | "world";

const ACTION_TYPE_META: Record<string, { label: string; labelWithState: string; url: string; urlWithState?: (s: string) => string }> = {
  contact_senator: {
    label: "Find Your Senators",
    labelWithState: "View Your Senators' Scores",
    url: "https://www.senate.gov/senators/senators-contact.htm",
    urlWithState: (s) => `/scorecard?branch=senate&state=${s}`,
  },
  contact_representative: {
    label: "Find Your Rep",
    labelWithState: "View Your Reps' Scores",
    url: "https://www.house.gov/representatives/find-your-representative",
    urlWithState: (s) => `/scorecard?branch=house&state=${s}`,
  },
  contact_whitehouse: {
    label: "Contact White House",
    labelWithState: "Contact White House",
    url: "https://www.whitehouse.gov/contact/",
  },
  public_comment: {
    label: "Submit Public Comment",
    labelWithState: "Submit Public Comment",
    url: "https://www.regulations.gov",
  },
  track_legislation: {
    label: "Track on Congress.gov",
    labelWithState: "Track on Congress.gov",
    url: "https://www.congress.gov",
  },
  register_vote: {
    label: "Register to Vote",
    labelWithState: "Register to Vote",
    url: "https://vote.gov",
    urlWithState: (s) => `https://vote.gov/register/${s.toLowerCase()}`,
  },
  attend_hearing: {
    label: "Find Town Halls",
    labelWithState: "Find Town Halls",
    url: "https://townhallproject.com",
  },
};

function getActionUrl(action: ActionItem, userState: string | null): string | null {
  const meta = ACTION_TYPE_META[action.type];
  if (userState && meta?.urlWithState) return meta.urlWithState(userState);
  if (action.url) return action.url;
  return meta?.url || null;
}

function getActionLabel(action: ActionItem, userState: string | null): string {
  const meta = ACTION_TYPE_META[action.type];
  if (!meta) return "TAKE ACTION";
  if (userState && meta.urlWithState) return `${meta.labelWithState} (${userState})`;
  return meta.label;
}

function useUserState(): [string | null, (s: string | null) => void] {
  const [state, setState] = useState<string | null>(null);
  useEffect(() => {
    const saved = localStorage.getItem("civitas_user_state");
    if (saved) setState(saved);
  }, []);
  const setAndPersist = (s: string | null) => {
    setState(s);
    if (s) localStorage.setItem("civitas_user_state", s);
    else localStorage.removeItem("civitas_user_state");
  };
  return [state, setAndPersist];
}

function StatePicker({
  userState,
  onSelect,
  compact = false,
}: {
  userState: string | null;
  onSelect: (s: string | null) => void;
  compact?: boolean;
}) {
  if (compact && userState) {
    return (
      <button
        onClick={() => onSelect(null)}
        className="text-[10px] font-pixel text-neon-cyan/60 hover:text-neon-cyan transition-colors"
        title="Change your state"
      >
        [{userState}] ✕
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="state-picker" className="text-[10px] font-pixel text-matrix-green/50">YOUR STATE:</label>
      <select
        id="state-picker"
        value={userState || ""}
        onChange={(e) => onSelect(e.target.value || null)}
        autoComplete="address-level1"
        className="appearance-none bg-crt-black border border-matrix-green/30 text-matrix-green font-pixel text-[11px] px-2 py-1 pr-6 cursor-pointer focus:outline-none focus:border-neon-cyan transition-all"
      >
        <option value="">SELECT</option>
        {STATES.map((s) => (
          <option key={s.code} value={s.code}>
            {s.code}
          </option>
        ))}
      </select>
    </div>
  );
}

function linkLabel(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    const known: Record<string, string> = {
      "senate.gov": "SENATE.GOV",
      "house.gov": "HOUSE.GOV",
      "whitehouse.gov": "WHITEHOUSE.GOV",
      "congress.gov": "CONGRESS.GOV",
      "regulations.gov": "REGULATIONS.GOV",
      "vote.gov": "VOTE.GOV",
      "townhallproject.com": "TOWNHALLPROJECT.COM",
    };
    return known[host] || host.toUpperCase();
  } catch {
    return "LINK";
  }
}

function ActionItemCard({
  action,
  userState,
}: {
  action: ActionItem;
  userState: string | null;
}) {
  const url = getActionUrl(action, userState);
  const stateLabel = getActionLabel(action, userState);
  const isInternal = url?.startsWith("/");
  const site = url && !isInternal ? linkLabel(url) : null;

  return (
    <div className="terminal-window p-3 border-l-2 border-l-neon-cyan/40 flex flex-col gap-2">
      <span className="text-sm text-matrix-green/80">{action.text}</span>
      {url && (
        <div className="flex items-center gap-2 flex-wrap">
          {isInternal ? (
            <Link
              href={url}
              className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan border border-neon-cyan/30 hover:border-neon-cyan/60 px-2 py-1 transition-colors flex items-center gap-1"
            >
              {stateLabel} →
            </Link>
          ) : (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan border border-neon-cyan/30 hover:border-neon-cyan/60 px-2 py-1 transition-colors flex items-center gap-1"
            >
              {site} <span aria-hidden="true">↗</span>
              <span className="sr-only"> (opens in new tab)</span>
            </a>
          )}
        </div>
      )}
    </div>
  );
}

const TABS: { id: Tab; label: string; color: string }[] = [
  { id: "issues", label: "ISSUES", color: "text-neon-cyan border-neon-cyan" },
  { id: "monitors", label: "MONITORS", color: "text-amber-400 border-amber-400" },
  { id: "elections", label: "ELECTIONS", color: "text-neon-yellow border-neon-yellow" },
  { id: "senate", label: "SENATE", color: "text-neon-yellow/70 border-neon-yellow/70" },
  { id: "house", label: "HOUSE", color: "text-neon-pink border-neon-pink" },
  { id: "executive", label: "EXECUTIVE", color: "text-orange-400 border-orange-400" },
  { id: "world", label: "WORLD", color: "text-green-400 border-green-400" },
];

function PolicyBadge({ area, themed = false }: { area: string; themed?: boolean }) {
  return (
    <span className={`text-[10px] px-2 py-0.5 border font-pixel ${themed ? "theme-tag" : "border-neon-yellow/30 text-neon-yellow/80 bg-neon-yellow/5"}`}>
      {area}
    </span>
  );
}

function SourceBadge({ name, url }: { name: string; url?: string }) {
  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[10px] px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/30 transition-colors"
      >
        {name} <span aria-hidden="true">↗</span>
        <span className="sr-only"> (opens in new tab)</span>
      </a>
    );
  }
  return (
    <span className="text-[10px] px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50">
      {name}
    </span>
  );
}

function SenatorChips({ issue }: { issue: ActionIssue }) {
  if (!issue.relatedSenators || issue.relatedSenators.length === 0) return null;

  return (
    <div className="mb-6">
      <h3 className="font-pixel text-sm text-neon-pink/80 mb-3">
        {">"} INVOLVED REPRESENTATIVES
      </h3>
      <div className="flex flex-wrap gap-2">
        {issue.relatedSenators.map((s) => (
          <Link
            key={s.id}
            href={`/scorecard?branch=senate&state=${s.state}&senator=${s.id}`}
            className={`flex items-center gap-2 px-3 py-2 border ${PARTY_BORDER[s.party]} bg-matrix-dark-green/20 hover:border-neon-cyan/50 transition-all group`}
          >
            <span className={`font-pixel text-[10px] ${PARTY_COLORS[s.party]}`}>
              [{s.party}-{s.state}]
            </span>
            <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green">
              {s.name}
            </span>
            <span className="text-[10px] font-pixel text-neon-cyan/60 border-l border-matrix-green/20 pl-2">
              SCORE: {Math.round(s.overallScore)}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function HeroIssue({
  issue,
  userState,
  themed = false,
  theme,
}: {
  issue: ActionIssue;
  userState: string | null;
  themed?: boolean;
  theme?: DailyTheme | null;
}) {
  const panelClass = themed
    ? "theme-hero-panel terminal-window border p-6 sm:p-8"
    : "terminal-window border-t-2 border-t-neon-cyan/50 p-6 sm:p-8";

  return (
    <div className={panelClass}>
      {/* Corner accents */}
      {themed && (
        <>
          <div className="theme-corner theme-corner--tl" aria-hidden="true" />
          <div className="theme-corner theme-corner--tr" aria-hidden="true" />
          <div className="theme-corner theme-corner--bl" aria-hidden="true" />
          <div className="theme-corner theme-corner--br" aria-hidden="true" />
        </>
      )}

      {/* Urgency strip */}
      {themed && <div className="theme-urgency-bar mb-5" aria-hidden="true" />}

      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <span className={`font-pixel text-xs px-2 py-1 border ${themed ? "theme-tag" : "text-neon-cyan/60 bg-neon-cyan/10 border-neon-cyan/30"}`}>
          TOP ISSUE
        </span>
        {themed && theme?.mood && (
          <span className="theme-mood-badge font-pixel">
            <span className="theme-mood-dot" aria-hidden="true" />
            {theme.mood}
          </span>
        )}
        <span className="text-xs text-matrix-green/40">{issue.date}</span>
      </div>

      <h2 className={`font-pixel text-lg sm:text-2xl mb-4 leading-relaxed ${themed ? "theme-accent-text theme-accent-glow" : "text-matrix-green"}`}>
        {issue.title}
      </h2>

      {/* Data strip separator */}
      {themed && <div className="theme-data-strip mb-4" aria-hidden="true" />}

      <p className="text-matrix-green/80 text-sm sm:text-base leading-relaxed mb-6">
        {issue.summary}
      </p>

      {issue.policyAreas.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap mb-6">
          {issue.policyAreas.map((area) => (
            <PolicyBadge key={area} area={area} />
          ))}
        </div>
      )}

      <SenatorChips issue={issue} />

      {issue.facts.length > 0 && (
        <div className="mb-6">
          <h3 className="font-pixel text-sm text-neon-yellow mb-3">
            {">"} KEY FACTS
          </h3>
          <div className="space-y-2">
            {issue.facts.map((fact, i) => (
              <div key={i} className="flex gap-3 text-sm">
                <span className="text-neon-yellow/60 shrink-0 font-pixel">[{i + 1}]</span>
                <span className="text-matrix-green/80">{fact}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {issue.actions.length > 0 && (
        <div className="mb-6">
          <h3 className="font-pixel text-sm text-neon-cyan mb-3">
            {">"} WHAT YOU CAN DO
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {issue.actions.map((action, i) => (
              <ActionItemCard
                key={i}
                action={typeof action === "string" ? { text: action, type: "general" } : action}
                userState={userState}
              />
            ))}
          </div>
        </div>
      )}

      {issue.relatedBills && issue.relatedBills.length > 0 && (
        <div className="mb-6">
          <h3 className="font-pixel text-sm text-neon-yellow/80 mb-3">
            {">"} OFFICIAL LEGISLATION
          </h3>
          <div className="space-y-2">
            {issue.relatedBills.map((bill) => (
              <a
                key={bill.id}
                href={bill.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 p-3 border border-neon-yellow/20 bg-neon-yellow/5 hover:border-neon-yellow/40 hover:bg-neon-yellow/10 transition-all group"
              >
                <span className="text-[10px] font-pixel text-neon-yellow/60 border border-neon-yellow/30 px-1.5 py-0.5 shrink-0">
                  {bill.id}
                </span>
                <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green truncate">
                  {bill.name}
                </span>
                <span className="text-[10px] font-pixel text-neon-cyan/50 shrink-0 ml-auto">
                  CONGRESS.GOV ↗
                </span>
              </a>
            ))}
          </div>
        </div>
      )}

      {issue.relatedExploreDocs.length > 0 && (
        <div className="mb-4">
          <h3 className="font-pixel text-sm text-matrix-green/60 mb-3">
            {">"} RELATED DOCUMENTS
          </h3>
          <div className="space-y-1.5">
            {issue.relatedExploreDocs.map((doc) => (
              <div key={doc.id} className="flex items-center gap-2 text-sm">
                <span className="text-[10px] px-1 py-0.5 border border-matrix-green/20 text-matrix-green/40 font-pixel">
                  {doc.docType.replace(/_/g, " ")}
                </span>
                {doc.url ? (
                  <a
                    href={doc.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-neon-cyan/70 hover:text-neon-cyan transition-colors truncate"
                  >
                    {doc.title}
                  </a>
                ) : (
                  <Link
                    href={`/explore/${doc.id}`}
                    className="text-neon-cyan/70 hover:text-neon-cyan transition-colors truncate"
                  >
                    {doc.title}
                  </Link>
                )}
                <span className="text-matrix-green/30 text-[10px] shrink-0">{doc.date}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {issue.sourceNames.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap pt-4 border-t border-matrix-green/10">
          <span className="text-[10px] text-matrix-green/30">SOURCES:</span>
          {issue.sourceNames.map((name, i) => (
            <SourceBadge key={name} name={name} url={issue.sourceUrls?.[i]} />
          ))}
        </div>
      )}
    </div>
  );
}

function SecondaryIssue({
  issue,
  userState,
}: {
  issue: ActionIssue;
  userState: string | null;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="terminal-window">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left p-4 sm:p-5 flex items-start justify-between gap-4"
        aria-expanded={expanded}
        aria-controls={`issue-detail-${issue.id}`}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-2">
            {issue.policyAreas.map((area) => (
              <PolicyBadge key={area} area={area} />
            ))}
          </div>
          <h3 className="font-pixel text-sm sm:text-base text-matrix-green leading-relaxed">
            {issue.title}
          </h3>
          {!expanded && (
            <p className="text-matrix-green/50 text-sm mt-1 line-clamp-2">
              {issue.summary}
            </p>
          )}
        </div>
        <span className="text-matrix-green/40 shrink-0 font-pixel text-sm mt-1" aria-hidden="true">
          {expanded ? "[-]" : "[+]"}
        </span>
      </button>

      {expanded && (
        <div id={`issue-detail-${issue.id}`} className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-matrix-green/10 pt-4 space-y-4">
          <p className="text-matrix-green/80 text-sm leading-relaxed">
            {issue.summary}
          </p>

          {issue.facts.length > 0 && (
            <div>
              <h4 className="font-pixel text-xs text-neon-yellow/60 mb-2">KEY FACTS</h4>
              <div className="space-y-1.5">
                {issue.facts.map((fact, i) => (
                  <div key={i} className="flex gap-2 text-sm">
                    <span className="text-neon-yellow/50 shrink-0 font-pixel text-[10px]">[{i + 1}]</span>
                    <span className="text-matrix-green/70">{fact}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {issue.actions.length > 0 && (
            <div>
              <h4 className="font-pixel text-xs text-neon-cyan/60 mb-2">WHAT YOU CAN DO</h4>
              <div className="space-y-2">
                {issue.actions.map((action, i) => (
                  <ActionItemCard
                    key={i}
                    action={typeof action === "string" ? { text: action, type: "general" } : action}
                    userState={userState}
                  />
                ))}
              </div>
            </div>
          )}

          {issue.relatedBills && issue.relatedBills.length > 0 && (
            <div>
              <h4 className="font-pixel text-xs text-neon-yellow/60 mb-2">OFFICIAL LEGISLATION</h4>
              <div className="space-y-1.5">
                {issue.relatedBills.map((bill) => (
                  <a
                    key={bill.id}
                    href={bill.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 p-2 border border-neon-yellow/15 bg-neon-yellow/5 hover:border-neon-yellow/30 transition-colors text-sm"
                  >
                    <span className="text-[10px] font-pixel text-neon-yellow/60 shrink-0">{bill.id}</span>
                    <span className="text-matrix-green/70 truncate">{bill.name}</span>
                    <span className="text-[10px] text-neon-cyan/40 shrink-0 ml-auto">↗</span>
                  </a>
                ))}
              </div>
            </div>
          )}

          {issue.relatedSenators && issue.relatedSenators.length > 0 && (
            <div>
              <h4 className="font-pixel text-xs text-neon-pink/60 mb-2">INVOLVED REPRESENTATIVES</h4>
              <div className="flex flex-wrap gap-2">
                {issue.relatedSenators.map((s) => (
                  <Link
                    key={s.id}
                    href={`/scorecard?branch=senate&state=${s.state}&senator=${s.id}`}
                    className={`flex items-center gap-1.5 px-2 py-1 border ${PARTY_BORDER[s.party]} bg-matrix-dark-green/20 hover:border-neon-cyan/40 transition-colors text-sm`}
                  >
                    <span className={`font-pixel text-[10px] ${PARTY_COLORS[s.party]}`}>[{s.party}]</span>
                    <span className="text-matrix-green/70">{s.name}</span>
                    <span className="text-[10px] font-pixel text-neon-cyan/50">{Math.round(s.overallScore)}</span>
                  </Link>
                ))}
              </div>
            </div>
          )}

          {issue.sourceNames.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap pt-3 border-t border-matrix-green/10">
              <span className="text-[10px] text-matrix-green/30">SOURCES:</span>
              {issue.sourceNames.map((name, i) => (
                <SourceBadge key={name} name={name} url={issue.sourceUrls?.[i]} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function sanitizeLLMCss(raw: string): string {
  const allowed: string[] = [];
  const blocks = raw.split(/(?=\.theme-hero-panel|@keyframes|@media)/g);

  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;

    const isPseudo = /^\.theme-hero-panel\s*::/.test(trimmed);
    const isKeyframes = /^@keyframes\s+theme-/.test(trimmed);
    const isMedia = /^@media\s/.test(trimmed);

    if (isPseudo || isKeyframes || isMedia) {
      allowed.push(trimmed);
    }
  }

  return allowed.join("\n\n");
}

function ThemeStyleInjector({ theme }: { theme: DailyTheme }) {
  const glow = theme.glowIntensity;
  const speed = theme.animationSpeed;
  const alt = theme.accentAlt || theme.accent;

  const baseCss = `
    :root {
      --theme-accent: ${theme.accent};
      --theme-accent-alt: ${alt};
      --theme-glow: ${glow};
      --theme-speed: ${speed}s;
    }

    @keyframes theme-border-glow {
      0%, 100% { box-shadow: 0 0 ${8 * glow}px ${theme.accent}20, inset 0 0 ${12 * glow}px ${theme.accent}08; border-color: ${theme.accent}30; }
      50% { box-shadow: 0 0 ${20 * glow}px ${theme.accent}35, inset 0 0 ${25 * glow}px ${theme.accent}12; border-color: ${theme.accent}60; }
    }
    @keyframes theme-accent-pulse {
      0%, 100% { opacity: 0.5; }
      50% { opacity: 1; }
    }
    @keyframes theme-urgency-crawl {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(100%); }
    }
    @keyframes theme-corner-flash {
      0%, 70%, 100% { opacity: 0.3; }
      80% { opacity: 0.8; }
    }

    .theme-hero-panel {
      position: relative;
      overflow: hidden;
      border-left: 3px solid ${theme.accent}80;
      border-color: ${theme.accent}40;
      background:
        linear-gradient(180deg, ${(theme.heroGradient?.[0]) || "#0a0a0f"} 0%, ${(theme.heroGradient?.[1]) || "#0d1117"} 50%, ${(theme.heroGradient?.[2]) || "#0a0f0a"} 100%);
      animation: theme-border-glow ${speed}s ease-in-out infinite;
    }
    .theme-hero-panel > * { position: relative; z-index: 2; }

    .theme-corner { position: absolute; width: 16px; height: 16px; pointer-events: none; z-index: 3; }
    .theme-corner--tl { top: 0; left: 0; border-top: 2px solid ${theme.accent}60; border-left: 2px solid ${theme.accent}60; animation: theme-corner-flash ${speed * 2}s ease-in-out infinite; }
    .theme-corner--tr { top: 0; right: 0; border-top: 2px solid ${theme.accent}60; border-right: 2px solid ${theme.accent}60; animation: theme-corner-flash ${speed * 2}s ease-in-out infinite ${speed * 0.5}s; }
    .theme-corner--bl { bottom: 0; left: 0; border-bottom: 2px solid ${alt}60; border-left: 2px solid ${alt}60; animation: theme-corner-flash ${speed * 2}s ease-in-out infinite ${speed}s; }
    .theme-corner--br { bottom: 0; right: 0; border-bottom: 2px solid ${alt}60; border-right: 2px solid ${alt}60; animation: theme-corner-flash ${speed * 2}s ease-in-out infinite ${speed * 1.5}s; }

    .theme-urgency-bar {
      position: relative;
      height: 2px;
      background: ${theme.accent}15;
      overflow: hidden;
    }
    .theme-urgency-bar::after {
      content: '';
      position: absolute;
      inset: 0;
      width: 40%;
      background: linear-gradient(90deg, transparent, ${theme.accent}80, ${alt}80, transparent);
      animation: theme-urgency-crawl ${speed * 1.5}s linear infinite;
    }

    .theme-mood-badge {
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
    .theme-mood-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: ${theme.accent};
      animation: theme-accent-pulse ${speed}s ease-in-out infinite;
    }

    .theme-accent-text { color: ${theme.accent}; }
    .theme-accent-border { border-color: ${theme.accent}40; }
    .theme-accent-glow { text-shadow: 0 0 ${10 * glow}px ${theme.accent}50, 0 0 ${30 * glow}px ${theme.accent}20; }

    .theme-section-line {
      height: 1px;
      background: linear-gradient(90deg, transparent, ${theme.accent}40, ${alt}40, transparent);
    }
    .theme-tag {
      border-color: ${theme.accent}30;
      color: ${theme.accent}cc;
      background: ${theme.accent}08;
    }

    .theme-data-strip {
      height: 1px;
      background: repeating-linear-gradient(90deg, ${theme.accent}20 0px, ${theme.accent}20 4px, transparent 4px, transparent 8px);
    }

    @media (prefers-reduced-motion: reduce) {
      .theme-hero-panel, .theme-hero-panel::before, .theme-hero-panel::after,
      .theme-urgency-bar::after, .theme-corner, .theme-mood-dot { animation: none !important; }
    }
  `;

  const customCss = theme.customCSS ? sanitizeLLMCss(theme.customCSS) : "";

  return <style dangerouslySetInnerHTML={{ __html: baseCss + "\n" + customCss }} />;
}

function MonitorsTab() {
  const [monitors, setMonitors] = useState<NationalMonitor[]>([]);
  const [selected, setSelected] = useState<NationalMonitorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchMonitors()
      .then((d) => setMonitors(d.monitors))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const openMonitor = useCallback((slug: string) => {
    setDetailLoading(true);
    fetchMonitorDetail(slug)
      .then((d) => {
        setSelected(d);
        setTimeout(() => {
          detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 100);
      })
      .catch(() => {})
      .finally(() => setDetailLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-amber-400 animate-pulse font-pixel text-sm">
          {">"} SCANNING NATIONAL CONCERNS...
        </div>
      </div>
    );
  }

  if (monitors.length === 0) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4">
        <div className="font-pixel text-sm text-amber-400/80">NO ACTIVE MONITORS</div>
        <p className="text-matrix-green/50 text-sm">
          National monitors are automatically created when an issue persists across
          multiple days in the news cycle. Check back as the system identifies
          ongoing concerns.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="text-center text-[10px] text-matrix-green/40 font-pixel mb-2">
        ONGOING NATIONAL CONCERNS — AUTO-DETECTED FROM RECURRING NEWS PATTERNS
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {monitors.map((m) => (
          <button
            key={m.slug}
            onClick={() => openMonitor(m.slug)}
            className={`terminal-window p-4 text-left transition-colors hover:border-amber-400/30 ${
              selected?.slug === m.slug ? "border-amber-400/50" : ""
            }`}
            aria-label={`View monitor: ${m.title}`}
          >
            <div className="flex items-center gap-2 mb-2">
              <span
                className={`w-2 h-2 rounded-full ${
                  m.status === "active" ? "bg-green-400" : "bg-amber-400/50"
                }`}
                aria-label={m.status === "active" ? "Active" : "Watching"}
              />
              <span className="font-pixel text-[10px] text-amber-400/60 uppercase">
                {m.category}
              </span>
            </div>
            <h3 className="font-pixel text-sm text-matrix-green mb-1 leading-relaxed">
              {m.title}
            </h3>
            <div className="flex items-center gap-3 text-[10px] text-matrix-green/40">
              <span>{m.updateCount} update{m.updateCount !== 1 ? "s" : ""}</span>
              {m.lastArticleDate && (
                <span>latest: {m.lastArticleDate}</span>
              )}
              <span>tracking since {m.createdAt}</span>
            </div>
          </button>
        ))}
      </div>

      {detailLoading && (
        <div className="flex items-center justify-center py-8">
          <div className="text-amber-400 animate-pulse font-pixel text-sm">
            {">"} LOADING TIMELINE...
          </div>
        </div>
      )}

      {selected && !detailLoading && (
        <div
          ref={detailRef}
          className="terminal-window border-t-2 border-t-amber-400/50 p-5 sm:p-6 scroll-mt-4"
          role="region"
          aria-label={`Monitor: ${selected.title}`}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="font-pixel text-base sm:text-lg text-amber-400">
                {selected.title}
              </h3>
              <div className="flex items-center gap-2 mt-1">
                <span
                  className={`text-[10px] font-pixel px-2 py-0.5 border ${
                    selected.status === "active"
                      ? "border-green-400/30 text-green-400/80"
                      : "border-amber-400/30 text-amber-400/60"
                  }`}
                >
                  {selected.status.toUpperCase()}
                </span>
                {selected.policyAreas.map((area) => (
                  <span
                    key={area}
                    className="text-[10px] font-pixel px-2 py-0.5 border border-neon-yellow/30 text-neon-yellow/80"
                  >
                    {area}
                  </span>
                ))}
              </div>
            </div>
            <button
              onClick={() => setSelected(null)}
              className="text-matrix-green/40 hover:text-matrix-green font-pixel text-xs"
              aria-label="Close monitor detail"
            >
              [CLOSE]
            </button>
          </div>

          <p className="text-matrix-green/70 text-sm mb-6 leading-relaxed">
            {selected.description}
          </p>

          <h4 className="font-pixel text-sm text-amber-400/80 mb-4">
            {">"} TIMELINE ({selected.updates.length} updates)
          </h4>

          <div className="relative pl-4 border-l border-amber-400/20 space-y-4">
            {selected.updates.map((update) => (
              <div key={update.id} className="relative">
                <div
                  className="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-amber-400/40 border border-amber-400/60"
                  aria-hidden="true"
                />
                <div className="text-[10px] text-matrix-green/40 font-pixel mb-1">
                  {update.date}
                  {update.sourceName && (
                    <span className="ml-2 text-matrix-green/30">via {update.sourceName}</span>
                  )}
                </div>
                <p className="text-sm text-matrix-green/80 leading-relaxed mb-1">
                  {update.summary}
                </p>
                <a
                  href={update.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] text-neon-cyan/60 hover:text-neon-cyan transition-colors"
                >
                  {update.articleTitle || "Source"} <span aria-hidden="true">↗</span>
                  <span className="sr-only"> (opens in new tab)</span>
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function IssuesTab({
  userState,
  setUserState,
}: {
  userState: string | null;
  setUserState: (s: string | null) => void;
}) {
  const [data, setData] = useState<ActionIssuesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showStatePicker, setShowStatePicker] = useState(false);

  useEffect(() => {
    fetchActionIssues()
      .then((d) => {
        setData(d);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const theme = data?.theme;

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-neon-cyan animate-pulse text-lg">{">"} SCANNING NEWS FEEDS...</div>
      </div>
    );
  }

  const heroIssue = data?.issues?.[0];
  const secondaryIssues = data?.issues?.slice(1) || [];

  if (!heroIssue) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-neon-yellow text-lg font-pixel mb-2">{">"} NO ISSUES YET</div>
        <p className="text-matrix-green/50 text-sm">Check back soon.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {theme && <ThemeStyleInjector theme={theme} />}

      {/* State selector bar */}
      <div className="flex items-center justify-between terminal-window p-3">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-pixel text-matrix-green/40">⚙ PERSONALIZE ACTIONS</span>
          {userState && (
            <span className="text-[10px] font-pixel text-neon-cyan/80 border border-neon-cyan/30 px-1.5 py-0.5 bg-neon-cyan/5">
              {STATES.find((s) => s.code === userState)?.name || userState} — links personalized
            </span>
          )}
        </div>
        {showStatePicker || !userState ? (
          <StatePicker
            userState={userState}
            onSelect={(s) => {
              setUserState(s);
              if (s) setShowStatePicker(false);
            }}
          />
        ) : (
          <StatePicker userState={userState} onSelect={setUserState} compact />
        )}
      </div>

      {/* Themed divider line above hero */}
      {theme && <div className="theme-section-line" aria-hidden="true" />}

      <HeroIssue issue={heroIssue} userState={userState} themed={!!theme} theme={theme} />

      {secondaryIssues.length > 0 && (
        <div>
          {theme && <div className="theme-section-line mb-4" aria-hidden="true" />}
          <h2 className="font-pixel text-sm text-matrix-green/50 mb-3 px-1">
            {">"} MORE ISSUES TO WATCH
          </h2>
          <div className="space-y-3">
            {secondaryIssues.map((issue) => (
              <SecondaryIssue key={issue.id} issue={issue} userState={userState} />
            ))}
          </div>
        </div>
      )}

      {theme && (
        <div className="text-center mt-2" aria-hidden="true">
          <div className="theme-section-line mb-3" />
          <div className="inline-flex items-center gap-3">
            <div className="theme-data-strip w-12" />
            <span
              className="text-[10px] font-pixel tracking-[0.2em] theme-accent-text"
              style={{ opacity: 0.4 }}
            >
              {theme.tagline.toUpperCase()}
            </span>
            <div className="theme-data-strip w-12" />
          </div>
        </div>
      )}
    </div>
  );
}

const DOC_TYPE_COLORS: Record<string, string> = {
  "Senate Floor Speech": "text-neon-yellow",
  "House Floor Speech": "text-neon-pink",
  "Executive Order": "text-orange-400",
  "Proclamation": "text-orange-300",
  "Proposed Rule": "text-neon-cyan",
  "Final Rule": "text-green-400",
  "Notice": "text-matrix-green",
};

function BranchTab({ branch }: { branch: string }) {
  const [docs, setDocs] = useState<BranchDocument[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchRecentByBranch(branch)
      .then((data) => setDocs(data.documents))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [branch]);

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-neon-cyan animate-pulse text-lg">
          {">"} LOADING {branch.toUpperCase()} ACTIVITY...
        </div>
      </div>
    );
  }

  if (docs.length === 0) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-matrix-green/50 text-lg">No recent activity found.</div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {docs.map((doc) => (
        <div key={doc.id} className="terminal-window p-4 hover:border-matrix-green/30 transition-colors">
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-1.5">
                <span className={`text-[10px] font-pixel ${DOC_TYPE_COLORS[doc.docType] || "text-matrix-green"}`}>
                  {doc.docType.toUpperCase()}
                </span>
                <span className="text-[10px] text-matrix-green/30">{doc.date}</span>
                {doc.politicianName && (
                  <span className="text-[10px] text-matrix-green/40">— {doc.politicianName}</span>
                )}
              </div>
              {doc.url ? (
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-matrix-green/80 hover:text-neon-cyan transition-colors leading-relaxed"
                >
                  {doc.title}
                </a>
              ) : (
                <Link
                  href={`/explore/${doc.id}`}
                  className="text-sm text-matrix-green/80 hover:text-neon-cyan transition-colors leading-relaxed"
                >
                  {doc.title}
                </Link>
              )}
              {doc.summary && (
                <p className="text-[11px] text-matrix-green/40 mt-1 line-clamp-2">{doc.summary}</p>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

const VALID_TABS = new Set<string>(["issues", "monitors", "elections", "senate", "house", "executive", "world"]);

export default function ActionPage() {
  return (
    <Suspense>
      <ActionPageInner />
    </Suspense>
  );
}

function ActionPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const initialTab = searchParams.get("tab");
  const [activeTab, setActiveTabRaw] = useState<Tab>(
    initialTab && VALID_TABS.has(initialTab) ? (initialTab as Tab) : "issues",
  );
  const [userState, setUserState] = useUserState();

  const setActiveTab = useCallback(
    (tab: Tab) => {
      setActiveTabRaw(tab);
      const url = tab === "issues" ? "/action" : `/action?tab=${tab}`;
      router.replace(url, { scroll: false });
    },
    [router],
  );

  return (
    <>
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-4xl mx-auto relative z-10">
          <div className="text-center mb-6">
            <GlitchText
              text="ACTION CENTER"
              as="h1"
              className="font-pixel text-xl sm:text-3xl md:text-4xl text-matrix-green animate-pulse-neon"
            />
            <p className="text-matrix-green/50 text-sm mt-3 max-w-xl mx-auto">
              Stay informed. Take action. Track your government.
            </p>
          </div>

          {/* Tab bar */}
          <div
            role="tablist"
            aria-label="Action Center sections"
            className="flex gap-1 mb-8 overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0"
            onKeyDown={(e) => {
              const tabs = TABS.map((t) => t.id);
              const idx = tabs.indexOf(activeTab);
              if (e.key === "ArrowRight") {
                e.preventDefault();
                setActiveTab(tabs[(idx + 1) % tabs.length]);
              } else if (e.key === "ArrowLeft") {
                e.preventDefault();
                setActiveTab(tabs[(idx - 1 + tabs.length) % tabs.length]);
              } else if (e.key === "Home") {
                e.preventDefault();
                setActiveTab(tabs[0]);
              } else if (e.key === "End") {
                e.preventDefault();
                setActiveTab(tabs[tabs.length - 1]);
              }
            }}
          >
            {TABS.map((tab) => (
              <button
                key={tab.id}
                role="tab"
                id={`tab-${tab.id}`}
                aria-selected={activeTab === tab.id}
                aria-controls={`tabpanel-${tab.id}`}
                tabIndex={activeTab === tab.id ? 0 : -1}
                onClick={() => setActiveTab(tab.id)}
                className={`font-pixel text-[10px] sm:text-xs px-3 sm:px-5 py-2.5 border-b-2 transition-all whitespace-nowrap ${
                  activeTab === tab.id
                    ? `${tab.color} bg-matrix-dark-green/30 border-current`
                    : "text-matrix-green/40 border-transparent hover:text-matrix-green/60 hover:border-matrix-green/20"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab panels */}
          <div
            role="tabpanel"
            id={`tabpanel-${activeTab}`}
            aria-labelledby={`tab-${activeTab}`}
            tabIndex={0}
          >
            {activeTab === "issues" && <IssuesTab userState={userState} setUserState={setUserState} />}
            {activeTab === "monitors" && <MonitorsTab />}
            {activeTab === "elections" && <ElectionsTab />}
            {activeTab === "senate" && <BranchTab branch="senate" />}
            {activeTab === "house" && <BranchTab branch="house" />}
            {activeTab === "executive" && <BranchTab branch="executive" />}
            {activeTab === "world" && <GlobeTab />}
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
