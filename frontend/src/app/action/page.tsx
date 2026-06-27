"use client";

import { Suspense, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import dynamic from "next/dynamic";
import MatrixRain from "@/components/effects/MatrixRain";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import GlitchText from "@/components/effects/GlitchText";
import { fetchActionIssues, fetchOpenComments, OpenCommentItem } from "@/lib/api";
import { useUserState } from "@/hooks/useUserState";
import { safeHref } from "@/lib/formatting";
import StancePulse from "@/components/action/StancePulse";
import { LogActionButton } from "@/components/action/CivicTracker";
import ShareButtons from "@/components/action/ShareButtons";
import BackToTop from "@/components/BackToTop";

const CivicActionWidget = dynamic(
  () => import("@/components/action/CivicTracker"),
  { ssr: false },
);
import type { ActionIssue, ActionIssuesResponse, DailyTheme } from "@/types/action";
import { STATES } from "@/data/states";

const GlobeTab = dynamic(() => import("@/components/action/GlobeTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-cyan/50 font-mono text-xs tracking-widest animate-pulse">LOADING GLOBE...</div>
    </div>
  ),
});

const ElectionsTab = dynamic(() => import("@/components/action/ElectionsTab"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-yellow/50 font-mono text-xs tracking-widest animate-pulse">LOADING ELECTIONS...</div>
    </div>
  ),
});

const MonitorsTab = dynamic(() => import("./MonitorsTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-amber-400/50 font-mono text-xs tracking-widest animate-pulse">SCANNING NATIONAL CONCERNS...</div>
    </div>
  ),
});

const TimelineTab = dynamic(() => import("./TimelineTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-purple-400/50 font-mono text-xs tracking-widest animate-pulse">LOADING TIMELINE...</div>
    </div>
  ),
});

const MyRepsTab = dynamic(() => import("@/components/action/MyRepsTab"), {
  loading: () => (
    <div className="flex items-center justify-center py-24">
      <div className="text-neon-pink/50 font-mono text-xs tracking-widest animate-pulse">LOADING REPRESENTATIVES...</div>
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

type Tab = "issues" | "my-reps" | "monitors" | "timeline" | "elections" | "world";

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
        className="text-[10px] font-mono tracking-widest text-neon-cyan/60 hover:text-neon-cyan transition-colors"
        title="Change your state"
        aria-label="Change your state"
      >
        {userState} ✕
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="state-picker" className="text-[10px] font-mono tracking-widest text-matrix-green/40">YOUR STATE</label>
      <select
        id="state-picker"
        value={userState || ""}
        onChange={(e) => onSelect(e.target.value || null)}
        autoComplete="address-level1"
        className="appearance-none bg-crt-black border border-matrix-green/25 text-matrix-green font-mono text-[11px] px-2 py-1 pr-6 cursor-pointer focus:outline-none focus:border-neon-cyan transition-all"
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

const TABS: { id: Tab; label: string; color: string }[] = [
  { id: "issues", label: "ISSUES", color: "text-neon-cyan border-neon-cyan" },
  { id: "my-reps", label: "MY REPS", color: "text-neon-pink border-neon-pink" },
  { id: "monitors", label: "MONITORS", color: "text-amber-400 border-amber-400" },
  { id: "timeline", label: "TIMELINE", color: "text-purple-400 border-purple-400" },
  { id: "elections", label: "ELECTIONS", color: "text-neon-yellow border-neon-yellow" },
  { id: "world", label: "GLOBE", color: "text-green-400 border-green-400" },
];

function PolicyBadge({ area, themed = false }: { area: string; themed?: boolean }) {
  return (
    <span className={`text-[10px] px-2 py-0.5 border font-mono tracking-wide ${themed ? "theme-tag" : "border-neon-yellow/25 text-neon-yellow/70 bg-neon-yellow/5"}`}>
      {area}
    </span>
  );
}

function SourceBadge({ name, url }: { name: string; url?: string }) {
  if (url) {
    return (
      <a
        href={safeHref(url) || "#"}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[10px] px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/30 transition-colors"
      >
        {name} <span aria-hidden="true">↗</span>
      </a>
    );
  }
  return (
    <span className="text-[10px] px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50">
      {name}
    </span>
  );
}

function SenatorChips({ issue, userState }: { issue: ActionIssue; userState: string | null }) {
  const senators = issue.relatedSenators ?? [];

  if (senators.length === 0 && !userState) return null;

  const contactUrl = (s: (typeof senators)[0]) =>
    s.contactFormUrl || s.websiteUrl || null;

  const scoreUrl = (s: (typeof senators)[0]) =>
    `/scorecard?branch=${s.chamber === "house" ? "house" : "senate"}&state=${s.state}&${s.chamber === "house" ? "rep" : "senator"}=${s.id}`;

  return (
    <div className="mb-6">
      <h3 className="font-mono text-[10px] tracking-widest text-neon-pink/60 mb-3 uppercase">
        {senators.length > 0 ? "Contact Representatives" : "Contact Your Representatives"}
      </h3>

      {senators.length > 0 ? (
        <div className="space-y-2">
          {senators.map((s) => {
            const url = contactUrl(s);
            return (
              <div
                key={s.id}
                className={`flex items-center gap-3 px-3 py-2.5 border ${PARTY_BORDER[s.party]} bg-matrix-dark-green/20`}
              >
                <span className={`font-mono text-[10px] shrink-0 ${PARTY_COLORS[s.party]}`}>
                  {s.party}-{s.state}
                </span>
                <span className="text-sm text-matrix-green/80 flex-1 min-w-0 truncate">
                  {s.name}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  {url ? (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] font-mono tracking-widest text-neon-cyan border border-neon-cyan/40 hover:border-neon-cyan hover:bg-neon-cyan/10 px-2 py-1 transition-colors"
                    >
                      CONTACT ↗
                    </a>
                  ) : (
                    <a
                      href={`https://www.senate.gov/senators/senators-contact.htm`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] font-mono tracking-widest text-neon-cyan/60 border border-neon-cyan/30 hover:border-neon-cyan/60 px-2 py-1 transition-colors"
                    >
                      CONTACT ↗
                    </a>
                  )}
                  <Link
                    href={scoreUrl(s)}
                    className="text-[10px] font-mono tracking-wide text-matrix-green/50 hover:text-matrix-green transition-colors"
                  >
                    SCORE: {Math.round(s.overallScore)}
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      ) : userState ? (
        <a
          href={`/scorecard?branch=senate&state=${userState}`}
          className="inline-flex items-center gap-2 text-[10px] font-mono tracking-widest text-neon-cyan border border-neon-cyan/40 hover:border-neon-cyan hover:bg-neon-cyan/10 px-3 py-1.5 transition-colors"
        >
          VIEW {userState} SENATORS &amp; CONTACT INFO →
        </a>
      ) : (
        <a
          href="https://www.senate.gov/senators/senators-contact.htm"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-[10px] font-mono tracking-widest text-neon-cyan border border-neon-cyan/40 hover:border-neon-cyan hover:bg-neon-cyan/10 px-3 py-1.5 transition-colors"
        >
          FIND YOUR SENATORS ↗
        </a>
      )}
    </div>
  );
}

function MonitorLinks({ slugs, onNavigate }: { slugs?: string[]; onNavigate?: (tab: Tab) => void }) {
  if (!slugs || slugs.length === 0) return null;
  return (
    <div className="flex items-center gap-2 flex-wrap mb-4">
      <span className="font-mono text-[10px] tracking-widest text-amber-400/40">TRACKING</span>
      {slugs.map((slug) => (
        <button
          key={slug}
          onClick={() => onNavigate?.("monitors")}
          className="text-[10px] font-mono tracking-wide px-2 py-0.5 border border-amber-400/25 text-amber-400/60 hover:text-amber-400/90 hover:border-amber-400/50 transition-colors bg-amber-400/5"
        >
          {slug.replace(/-/g, " ").slice(0, 40)}
          {slug.length > 40 ? "…" : ""}
        </button>
      ))}
    </div>
  );
}

function HeroIssue({
  issue,
  userState,
  themed = false,
  onNavigate,
  isDeepLinked = false,
}: {
  issue: ActionIssue;
  userState: string | null;
  themed?: boolean;
  onNavigate?: (tab: Tab) => void;
  isDeepLinked?: boolean;
}) {
  const heroRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isDeepLinked && heroRef.current) {
      heroRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [isDeepLinked]);

  const panelClass = themed
    ? "theme-hero-panel terminal-window border p-6 sm:p-8"
    : "terminal-window border-t-2 border-t-neon-cyan/50 p-6 sm:p-8";

  return (
    <div ref={heroRef} className={panelClass}>
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
        <span className={`font-mono text-[10px] tracking-widest px-2 py-1 border ${themed ? "theme-tag" : "text-neon-cyan/60 bg-neon-cyan/10 border-neon-cyan/30"}`}>
          TOP ISSUE
        </span>
        <span className="text-[11px] font-mono text-matrix-green/35">{issue.date}</span>
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

      <MonitorLinks slugs={issue.relatedMonitorSlugs} onNavigate={onNavigate} />

      <SenatorChips issue={issue} userState={userState} />

      {issue.facts.length > 0 && (
        <div className="mb-6">
          <h3 className="font-mono text-[10px] tracking-widest text-neon-yellow/60 mb-3 uppercase">
            Key Facts
          </h3>
          <div className="space-y-2">
            {issue.facts.map((fact, i) => (
              <div key={i} className="flex gap-3 text-sm">
                <span className="text-neon-yellow/40 shrink-0 font-mono text-[10px] mt-0.5">{i + 1}.</span>
                <span className="text-matrix-green/80">{fact}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Specific actions only — senator contact handled above by SenatorChips */}
      {issue.actions.filter(a => a.type === "track_legislation" && a.url).length > 0 && (
        <div className="mb-6">
          <h3 className="font-mono text-[10px] tracking-widest text-neon-cyan/60 mb-3 uppercase">
            Track Legislation
          </h3>
          <div className="space-y-2">
            {issue.actions
              .filter(a => a.type === "track_legislation" && a.url)
              .map((action, i) => (
                <a
                  key={i}
                  href={action.url!}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 p-3 border border-neon-cyan/20 bg-neon-cyan/5 hover:border-neon-cyan/40 hover:bg-neon-cyan/10 transition-all group"
                >
                  <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green flex-1">
                    {action.text}
                  </span>
                  <span className="text-[10px] font-mono tracking-wide text-neon-cyan/50 shrink-0">
                    CONGRESS.GOV ↗
                  </span>
                </a>
              ))}
          </div>
        </div>
      )}

      {issue.relatedBills && issue.relatedBills.length > 0 && (
        <div className="mb-6">
          <h3 className="font-mono text-[10px] tracking-widest text-neon-yellow/60 mb-3 uppercase">
            Official Legislation
          </h3>
          <div className="space-y-2">
            {issue.relatedBills.map((bill) => (
              <a
                key={bill.id}
                href={safeHref(bill.url) || "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 p-3 border border-neon-yellow/20 bg-neon-yellow/5 hover:border-neon-yellow/40 hover:bg-neon-yellow/10 transition-all group"
              >
                <span className="text-[10px] font-mono tracking-wide text-neon-yellow/60 border border-neon-yellow/30 px-1.5 py-0.5 shrink-0">
                  {bill.id}
                </span>
                <span className="text-sm text-matrix-green/80 group-hover:text-matrix-green truncate">
                  {bill.name}
                </span>
                <span className="text-[10px] font-mono tracking-wide text-neon-cyan/50 shrink-0 ml-auto">
                  CONGRESS.GOV ↗
                </span>
              </a>
            ))}
          </div>
        </div>
      )}

      {issue.relatedExploreDocs.length > 0 && (
        <div className="mb-4">
          <h3 className="font-mono text-[10px] tracking-widest text-matrix-green/40 mb-3 uppercase">
            Related Documents
          </h3>
          <div className="space-y-2">
            {issue.relatedExploreDocs.map((doc) => {
              const today = new Date().toISOString().slice(0, 10);
              const commentOpen = !!(doc.commentUrl && doc.commentsCloseOn && doc.commentsCloseOn >= today);
              return (
                <div key={doc.id} className="space-y-1">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-[10px] px-1 py-0.5 border border-matrix-green/20 text-matrix-green/40 font-mono tracking-wide shrink-0">
                      {doc.docType.replace(/_/g, " ")}
                    </span>
                    <Link
                      href={`/explore/${doc.id}`}
                      className="text-neon-cyan/70 hover:text-neon-cyan transition-colors truncate"
                    >
                      {doc.title}
                    </Link>
                    <span className="text-matrix-green/30 text-[10px] shrink-0">{doc.date}</span>
                  </div>
                  {commentOpen && (
                    <Link
                      href={`/explore/${doc.id}#comment`}
                      className="inline-flex items-center gap-1.5 text-[10px] font-mono tracking-wide
                                 text-neon-cyan/70 hover:text-neon-cyan border border-neon-cyan/30 hover:border-neon-cyan/60
                                 px-2 py-0.5 transition-colors"
                    >
                      → SUBMIT COMMENT
                    </Link>
                  )}
                </div>
              );
            })}
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

      <StancePulse
        issueId={issue.id}
        initialConcerned={issue.concernedCount || 0}
        initialNotPriority={issue.notPriorityCount || 0}
      />
      <div className="mt-3 flex items-center justify-between gap-3">
        <a
          href={`/issue/${issue.id}`}
          className="text-xs text-matrix-green border border-matrix-green/40 hover:border-matrix-green hover:bg-matrix-green/10 px-3 py-1.5 transition-colors"
        >
          READ FULL STORY →
        </a>
        <LogActionButton issueTitle={issue.title} />
      </div>

      <ShareButtons issue={issue} />
    </div>
  );
}

function SecondaryIssue({
  issue,
  userState,
  onNavigate,
  deepLinked = false,
  onToggle,
}: {
  issue: ActionIssue;
  userState: string | null;
  onNavigate?: (tab: Tab) => void;
  deepLinked?: boolean;
  onToggle?: (id: number, expanded: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(deepLinked);
  const cardRef = useRef<HTMLDivElement>(null);

  // If this issue is deep-linked, expand and scroll to it once data is ready
  useEffect(() => {
    if (deepLinked) {
      setExpanded(true);
      // Delay slightly so the panel renders before scrolling
      const t = setTimeout(() => {
        cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 100);
      return () => clearTimeout(t);
    }
  }, [deepLinked]);

  function handleToggle() {
    const next = !expanded;
    setExpanded(next);
    onToggle?.(issue.id, next);
  }

  return (
    <div ref={cardRef} className="terminal-window">
      <button
        onClick={handleToggle}
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
        <span className="text-matrix-green/40 shrink-0 font-mono text-base mt-0.5 leading-none" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded && (
        <div id={`issue-detail-${issue.id}`} className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-matrix-green/10 pt-4 space-y-4">
          <p className="text-matrix-green/80 text-sm leading-relaxed">
            {issue.summary}
          </p>

          <MonitorLinks slugs={issue.relatedMonitorSlugs} onNavigate={onNavigate} />

          {issue.facts.length > 0 && (
            <div>
              <h4 className="font-mono text-[10px] tracking-widest text-neon-yellow/50 mb-2 uppercase">Key Facts</h4>
              <div className="space-y-1.5">
                {issue.facts.map((fact, i) => (
                  <div key={i} className="flex gap-2 text-sm">
                    <span className="text-neon-yellow/40 shrink-0 font-mono text-[10px] mt-0.5">{i + 1}.</span>
                    <span className="text-matrix-green/70">{fact}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <SenatorChips issue={issue} userState={userState} />

          {issue.actions.filter(a => a.type === "track_legislation" && a.url).length > 0 && (
            <div>
              <h4 className="font-mono text-[10px] tracking-widest text-neon-cyan/50 mb-2 uppercase">Track Legislation</h4>
              <div className="space-y-1.5">
                {issue.actions
                  .filter(a => a.type === "track_legislation" && a.url)
                  .map((action, i) => (
                    <a
                      key={i}
                      href={action.url!}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 p-2 border border-neon-cyan/15 bg-neon-cyan/5 hover:border-neon-cyan/30 transition-colors text-sm"
                    >
                      <span className="text-matrix-green/70 flex-1 truncate">{action.text}</span>
                      <span className="text-[10px] text-neon-cyan/40 shrink-0 ml-auto">↗</span>
                    </a>
                  ))}
              </div>
            </div>
          )}

          {issue.relatedBills && issue.relatedBills.length > 0 && (
            <div>
              <h4 className="font-mono text-[10px] tracking-widest text-neon-yellow/50 mb-2 uppercase">Official Legislation</h4>
              <div className="space-y-1.5">
                {issue.relatedBills.map((bill) => (
                  <a
                    key={bill.id}
                    href={safeHref(bill.url) || "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 p-2 border border-neon-yellow/15 bg-neon-yellow/5 hover:border-neon-yellow/30 transition-colors text-sm"
                  >
                    <span className="text-[10px] font-mono tracking-wide text-neon-yellow/60 shrink-0">{bill.id}</span>
                    <span className="text-matrix-green/70 truncate">{bill.name}</span>
                    <span className="text-[10px] text-neon-cyan/40 shrink-0 ml-auto">↗</span>
                  </a>
                ))}
              </div>
            </div>
          )}

          {issue.relatedSenators && issue.relatedSenators.length > 0 && (
            <div>
              <h4 className="font-mono text-[10px] tracking-widest text-neon-pink/50 mb-2 uppercase">Representatives in Coverage</h4>
              <div className="flex flex-wrap gap-2">
                {issue.relatedSenators.map((s) => (
                  <Link
                    key={s.id}
                    href={`/scorecard?branch=${s.chamber === "house" ? "house" : "senate"}&state=${s.state}&${s.chamber === "house" ? "rep" : "senator"}=${s.id}`}
                    className={`flex items-start gap-1.5 px-2 py-1.5 border ${PARTY_BORDER[s.party]} bg-matrix-dark-green/20 hover:border-neon-cyan/40 transition-colors`}
                  >
                    <span className={`font-mono text-[10px] mt-0.5 shrink-0 ${PARTY_COLORS[s.party]}`}>{s.party}</span>
                    <div className="flex flex-col min-w-0">
                      <span className="text-sm text-matrix-green/70 leading-snug">{s.name}</span>
                      {s.matchReason && (
                        <span className="text-[10px] font-mono text-matrix-green/35 uppercase tracking-wide">
                          {s.matchReason}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] font-mono tracking-wide text-neon-cyan/50 mt-0.5 shrink-0">{Math.round(s.overallScore)}</span>
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

          <StancePulse
            issueId={issue.id}
            initialConcerned={issue.concernedCount || 0}
            initialNotPriority={issue.notPriorityCount || 0}
          />
          <div className="mt-3 flex justify-end">
            <LogActionButton issueTitle={issue.title} />
          </div>

          <ShareButtons issue={issue} />
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

function IssuesTab({
  userState,
  setUserState,
  onNavigate,
  initialDate,
  onDateChange,
  initialIssueId,
  onIssueChange,
}: {
  userState: string | null;
  setUserState: (s: string | null) => void;
  onNavigate?: (tab: Tab) => void;
  initialDate?: string | null;
  onDateChange?: (date: string | null) => void;
  initialIssueId?: number | null;
  onIssueChange?: (id: number | null) => void;
}) {
  const [data, setData] = useState<ActionIssuesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string | null>(initialDate || null);

  const loadIssues = useCallback((date?: string) => {
    setLoading(true);
    setFetchError(false);
    fetchActionIssues(date)
      .then((d) => setData(d))
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadIssues(initialDate || undefined);
  }, [loadIssues, initialDate]);

  const availableDates = useMemo(() => data?.availableDates || [], [data?.availableDates]);
  const currentDate = selectedDate || data?.date || null;
  const currentIdx = currentDate ? availableDates.indexOf(currentDate) : 0;

  const goToPrev = useCallback(() => {
    if (currentIdx < availableDates.length - 1) {
      const d = availableDates[currentIdx + 1];
      setSelectedDate(d);
      loadIssues(d);
      onDateChange?.(d);
    }
  }, [currentIdx, availableDates, loadIssues, onDateChange]);

  const goToNext = useCallback(() => {
    if (currentIdx > 0) {
      const d = availableDates[currentIdx - 1];
      setSelectedDate(d);
      loadIssues(d);
      onDateChange?.(d);
    } else if (currentIdx === 0 && selectedDate) {
      setSelectedDate(null);
      loadIssues();
      onDateChange?.(null);
    }
  }, [currentIdx, availableDates, selectedDate, loadIssues, onDateChange]);

  const theme = data?.theme;
  const generatedAt = data?.generatedAt;

  function formatGeneratedAt(iso: string): string {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit",
      });
    } catch {
      return "";
    }
  }

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-neon-cyan/50 font-mono text-xs tracking-widest animate-pulse">SCANNING NEWS FEEDS...</div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-6 text-center" role="alert">
        <div className="text-red-400 font-mono text-sm tracking-widest mb-2">CONNECTION ERROR</div>
        <p className="text-matrix-green/50 text-sm mb-4">Could not load today&apos;s issues.</p>
        <button
          onClick={() => loadIssues(selectedDate || undefined)}
          className="text-neon-cyan font-mono text-xs tracking-widest border border-neon-cyan/30 px-4 py-2 hover:bg-neon-cyan/10 transition-colors"
        >
          RETRY
        </button>
      </div>
    );
  }

  const heroIssue = data?.issues?.[0];
  const secondaryIssues = data?.issues?.slice(1) || [];

  if (!heroIssue) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-6 text-center" role="status" aria-live="polite">
        <div className="text-neon-yellow font-mono text-sm tracking-widest mb-2">NO ISSUES YET</div>
        <p className="text-matrix-green/50 text-sm">Check back soon.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {theme && <ThemeStyleInjector theme={theme} />}

      {/* Date navigation */}
      {availableDates.length > 1 && (
        <div className="flex items-center justify-center gap-4 font-mono text-[11px] tracking-widest">
          <button
            onClick={goToPrev}
            disabled={currentIdx >= availableDates.length - 1}
            className="text-matrix-green/50 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed transition-colors"
            aria-label="Previous day"
          >
            ← PREV
          </button>
          <span className="text-matrix-green/70 px-3 py-1 border border-matrix-green/15 bg-matrix-green/5 min-w-[110px] text-center">
            {currentDate || "—"}
          </span>
          <button
            onClick={goToNext}
            disabled={currentIdx <= 0 && !selectedDate}
            className="text-matrix-green/50 hover:text-matrix-green disabled:text-matrix-green/20 disabled:cursor-not-allowed transition-colors"
            aria-label="Next day"
          >
            NEXT →
          </button>
          {selectedDate && (
            <button
              onClick={() => { setSelectedDate(null); loadIssues(); onDateChange?.(null); }}
              className="text-neon-cyan/50 hover:text-neon-cyan transition-colors ml-1"
              aria-label="Jump to present"
            >
              LATEST
            </button>
          )}
        </div>
      )}

      {/* Data freshness timestamp */}
      {generatedAt && (
        <div className="text-center">
          <span className="text-matrix-green/30 text-[10px] font-mono">
            Updated: {formatGeneratedAt(generatedAt)}
          </span>
        </div>
      )}

      {/* State selector bar */}
      <div className="flex items-center justify-between terminal-window p-3">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono tracking-widest text-matrix-green/35">PERSONALIZE</span>
          {userState && (
            <span className="text-[10px] font-mono text-neon-cyan/70 border border-neon-cyan/20 px-1.5 py-0.5 bg-neon-cyan/5">
              {STATES.find((s) => s.code === userState)?.name || userState} — links personalized
            </span>
          )}
        </div>
        {!userState ? (
          <StatePicker userState={userState} onSelect={setUserState} />
        ) : (
          <StatePicker userState={userState} onSelect={setUserState} compact />
        )}
      </div>

      {/* Themed divider line above hero */}
      {theme && <div className="theme-section-line" aria-hidden="true" />}

      <HeroIssue
        issue={heroIssue}
        userState={userState}
        themed={!!theme}
        onNavigate={onNavigate}
        isDeepLinked={initialIssueId === heroIssue.id}
      />

      {secondaryIssues.length > 0 && (
        <div>
          {theme && <div className="theme-section-line mb-4" aria-hidden="true" />}
          <h2 className="font-mono text-[10px] tracking-[0.3em] text-matrix-green/40 mb-3 px-1 uppercase">
            More Issues to Watch
          </h2>
          <div className="space-y-3">
            {secondaryIssues.map((issue) => (
              <SecondaryIssue
                key={issue.id}
                issue={issue}
                userState={userState}
                onNavigate={onNavigate}
                deepLinked={initialIssueId === issue.id}
                onToggle={(id, expanded) => onIssueChange?.(expanded ? id : null)}
              />
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
              className="text-[10px] font-pixel tracking-[0.2em] theme-accent-text opacity-40"
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

const VALID_TABS = new Set<string>(["issues", "my-reps", "monitors", "timeline", "elections", "world"]);
function isValidTab(s: string | null): s is Tab {
  return s !== null && VALID_TABS.has(s);
}

function OpenCommentsBanner({ onCount }: { onCount?: (n: number) => void }) {
  const [items, setItems] = useState<OpenCommentItem[]>([]);

  useEffect(() => {
    fetchOpenComments()
      .then((data) => {
        setItems(data.slice(0, 4));
        onCount?.(data.length);
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (items.length === 0) return null;

  function daysLeft(closeDate: string): string {
    const diff = Math.ceil(
      (new Date(closeDate).getTime() - Date.now()) / 86400000
    );
    return diff <= 0 ? "closes today" : diff === 1 ? "1 day left" : `${diff} days left`;
  }

  return (
    <section aria-label="Open public comment periods" className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400/80 shrink-0" aria-hidden="true" />
        <span className="font-mono text-[10px] tracking-widest text-amber-400/70">OPEN FOR PUBLIC COMMENT</span>
        <div className="flex-1 h-px bg-amber-400/15" aria-hidden="true" />
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0 snap-x">
        {items.map((item) => (
          <div
            key={item.id}
            className="terminal-window border border-amber-400/30 bg-amber-400/5 p-3 min-w-[220px] max-w-[260px] flex-shrink-0 snap-start flex flex-col gap-1.5"
          >
            <p className="text-[11px] text-matrix-green/80 leading-snug line-clamp-3 flex-1">
              {item.title}
            </p>
            {item.agencyName && (
              <div className="text-[9px] text-amber-400/40 font-mono tracking-wider truncate">
                {item.agencyName}
              </div>
            )}
            <div className="flex items-center justify-between gap-2">
              <span className="text-[9px] text-amber-400/60 font-mono">
                {daysLeft(item.commentsCloseOn)}
              </span>
              <a
                href={item.commentUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[9px] tracking-widest text-amber-400/70 border border-amber-400/25 px-2 py-0.5 hover:bg-amber-400/10 transition-colors shrink-0"
              >
                COMMENT ↗
              </a>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

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

  const paramTab = searchParams.get("tab");
  const [activeTab, setActiveTabRaw] = useState<Tab>(
    isValidTab(paramTab) ? paramTab : "issues",
  );
  const [userState, setUserState] = useUserState();
  const [sharedIssues, setSharedIssues] = useState<ActionIssue[]>([]);
  const [openCommentCount, setOpenCommentCount] = useState(0);

  useEffect(() => {
    fetchActionIssues()
      .then((d) => setSharedIssues(d.issues))
      .catch(() => {/* silently ignore — IssuesTab has its own error handling */});
  }, []);

  // Parse ?issue=<id> from URL (numeric id)
  const paramIssue = searchParams.get("issue");
  const initialIssueId = paramIssue ? parseInt(paramIssue, 10) || null : null;

  useEffect(() => {
    const t = searchParams.get("tab");
    if (isValidTab(t) && t !== activeTab) {
      setActiveTabRaw(t);
    } else if (!t && activeTab !== "issues") {
      setActiveTabRaw("issues");
    }
  // activeTab intentionally omitted: including it would re-trigger the effect
  // on every user-initiated tab switch, creating a loop with setActiveTab.
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  const setActiveTab = useCallback(
    (tab: Tab) => {
      setActiveTabRaw(tab);
      const url = tab === "issues" ? "/action" : `/action?tab=${tab}`;
      router.replace(url, { scroll: false });
      requestAnimationFrame(() => {
        document.getElementById(`tabpanel-${tab}`)?.focus();
      });
    },
    [router],
  );

  // Update URL when a secondary issue is expanded/collapsed
  const handleIssueChange = useCallback(
    (id: number | null) => {
      const url = id ? `/action?issue=${id}` : "/action";
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
            <h1 className="font-pixel text-xl sm:text-3xl md:text-4xl text-matrix-green neon-green">ACTION CENTER</h1>
            <p className="text-matrix-green/40 text-xs font-mono tracking-wider mt-3 max-w-xl mx-auto">
              Stay informed. Take action. Track your government.
            </p>
          </div>

          {/* Open comment periods banner */}
          <OpenCommentsBanner onCount={setOpenCommentCount} />

          {/* Tab bar */}
          <div
            role="tablist"
            aria-label="Action Center sections"
            className="flex gap-1 mb-8 overflow-x-auto pb-1 -mx-4 px-4 sm:mx-0 sm:px-0 sticky top-16 z-30 bg-crt-black/95 backdrop-blur-sm"
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
                className={`font-mono text-[11px] tracking-widest px-3 sm:px-5 py-3 border-b-2 transition-all whitespace-nowrap ${
                  activeTab === tab.id
                    ? `${tab.color} bg-matrix-dark-green/20 border-current`
                    : "text-matrix-green/35 border-transparent hover:text-matrix-green/60 hover:border-matrix-green/15"
                }`}
              >
                {tab.label}
                {tab.id === "issues" && openCommentCount > 0 && (
                  <span className="ml-1.5 text-[9px] text-emerald-400/80 border border-emerald-500/40 px-1 py-0.5 rounded bg-emerald-500/10">
                    {openCommentCount} OPEN
                  </span>
                )}
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
            {activeTab === "issues" && <IssuesTab
              userState={userState}
              setUserState={setUserState}
              onNavigate={setActiveTab}
              initialDate={searchParams.get("date")}
              onDateChange={(d) => {
                const url = d ? `/action?date=${d}` : "/action";
                router.replace(url, { scroll: false });
              }}
              initialIssueId={initialIssueId}
              onIssueChange={handleIssueChange}
            />}
            {activeTab === "my-reps" && <MyRepsTab userState={userState} setUserState={setUserState} issues={sharedIssues} />}
            {activeTab === "monitors" && <MonitorsTab />}
            {activeTab === "timeline" && <TimelineTab />}
            {activeTab === "elections" && <ElectionsTab />}
            {activeTab === "world" && <GlobeTab />}
          </div>
        </div>
      </main>
      <Footer />
      <BackToTop />
      <CivicActionWidget />
    </>
  );
}
