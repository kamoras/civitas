"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import MatrixRain from "@/components/effects/MatrixRain";
import BranchSelector, { type Branch } from "@/components/BranchSelector";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import { fetchLeaderboard, fetchRepLeaderboard, fetchPresidentLeaderboard, fetchJusticeLeaderboard } from "@/lib/api";
import { calculateOverallScore, calculatePresidentScore, calculateJusticeScore, getScoreColor } from "@/lib/corruption";
import { useScoreWeights } from "@/hooks/useConfig";
import type { LeaderboardEntry, ScoreTrend } from "@/types/senator";
import type { PresidentLeaderboardEntry } from "@/types/president";
import type { JusticeLeaderboardEntry } from "@/types/justice";

type PartyFilter = "ALL" | "D" | "R" | "I";
type SortKey = "score" | "pac_dollars" | "pac_pct";

function formatDollars(n: number | undefined | null): string {
  const v = n ?? 0;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function partyColor(party: string): string {
  if (party === "D") return "text-dem-blue";
  if (party === "R") return "text-rep-red";
  return "text-ind-purple";
}

function partyBg(party: string): string {
  if (party === "D") return "bg-dem-blue/20 border-dem-blue/40";
  if (party === "R") return "bg-rep-red/20 border-rep-red/40";
  return "bg-ind-purple/20 border-ind-purple/40";
}

function rankColor(rank: number): string {
  if (rank === 1) return "text-matrix-green neon-green";
  if (rank === 2) return "text-neon-cyan";
  if (rank === 3) return "text-neon-yellow";
  if (rank <= 10) return "text-matrix-green/80";
  return "text-matrix-green/40";
}

function ScoreBar({ score }: { score: number }) {
  const color =
    score >= 81
      ? "bg-matrix-green"
      : score >= 61
        ? "bg-cyan-400"
        : score >= 41
          ? "bg-yellow-500"
          : score >= 21
            ? "bg-orange-500"
            : "bg-red-500";

  return (
    <div className="flex items-center gap-2">
      <div
        className="w-16 h-1.5 bg-white/10 rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={score}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Score: ${score} out of 100`}
      >
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className={`text-sm font-bold tabular-nums ${getScoreColor(score)}`} aria-hidden="true">{score}</span>
    </div>
  );
}

function TrendIndicator({ trend }: { trend?: ScoreTrend }) {
  if (!trend || trend.direction === "new") return null;

  const abs = Math.abs(trend.change);
  const formatted = abs >= 1 ? abs.toFixed(1) : abs.toFixed(2);

  if (trend.direction === "up") {
    return (
      <span
        className="inline-flex items-center gap-0.5 text-xs text-matrix-green"
        title={`Up ${formatted} pts from previous score of ${trend.previousScore?.toFixed(1) ?? "?"}`}
        aria-label={`Trending up ${formatted} points`}
      >
        <svg className="w-3 h-3" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
          <path d="M6 2L10 7H2L6 2Z" />
        </svg>
        {abs >= 1 && <span className="tabular-nums">{formatted}</span>}
      </span>
    );
  }

  if (trend.direction === "down") {
    return (
      <span
        className="inline-flex items-center gap-0.5 text-xs text-red-400"
        title={`Down ${formatted} pts from previous score of ${trend.previousScore?.toFixed(1) ?? "?"}`}
        aria-label={`Trending down ${formatted} points`}
      >
        <svg className="w-3 h-3" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
          <path d="M6 10L2 5H10L6 10Z" />
        </svg>
        {abs >= 1 && <span className="tabular-nums">{formatted}</span>}
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center text-xs text-white/30"
      title="Score stable (no significant change)"
      aria-label="Score stable"
    >
      <svg className="w-3 h-3" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
        <rect x="2" y="5" width="8" height="2" rx="1" />
      </svg>
    </span>
  );
}

const PRES_PARTY: Record<string, { label: string; color: string; bg: string }> = {
  D:  { label: "DEM", color: "text-dem-blue",   bg: "bg-dem-blue/20 border-dem-blue/40" },
  R:  { label: "REP", color: "text-rep-red",    bg: "bg-rep-red/20 border-rep-red/40" },
  DR: { label: "D-R", color: "text-teal-400",   bg: "bg-teal-400/20 border-teal-400/40" },
  F:  { label: "FED", color: "text-purple-400", bg: "bg-purple-400/20 border-purple-400/40" },
  W:  { label: "WHG", color: "text-amber-400",  bg: "bg-amber-400/20 border-amber-400/40" },
  I:  { label: "IND", color: "text-white/70",   bg: "bg-white/10 border-white/30" },
};

function presParty(party: string) {
  return PRES_PARTY[party] ?? { label: party, color: "text-white/50", bg: "bg-white/10 border-white/20" };
}

function termYears(start: string, end: string | null): string {
  const s = start.slice(0, 4);
  const e = end ? end.slice(0, 4) : "Present";
  return `${s}–${e}`;
}

function PresidentLeaderboard({
  entries,
  loading,
  error,
}: {
  entries: PresidentLeaderboardEntry[];
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="terminal-window p-8 text-center" role="status" aria-live="polite">
        <p className="text-matrix-green/60 font-mono text-xs tracking-widest animate-pulse">LOADING PRESIDENTIAL DATA...</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="terminal-window p-8 text-center border-red-500/40" role="alert">
        <p className="text-red-400">{">"} ERROR: {error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="terminal-window overflow-hidden">
        <TerminalTitlebar title={`president_leaderboard.db — ${entries.length} presidents`} />

        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm font-terminal">
            <thead>
              <tr className="border-b border-matrix-green/20 text-matrix-green/50 text-xs uppercase tracking-widest">
                <th scope="col" className="px-4 py-3 text-left w-14">RANK</th>
                <th scope="col" className="px-4 py-3 text-left">PRESIDENT</th>
                <th scope="col" className="px-3 py-3 text-center w-20">PARTY</th>
                <th scope="col" className="px-3 py-3 text-center w-24">TERM</th>
                <th scope="col" className="px-3 py-3 text-left w-36">SCORE</th>
                <th scope="col" className="px-3 py-3 text-right w-24">APPROVAL</th>
                <th scope="col" className="px-3 py-3 text-right w-24">GDP %</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, idx) => {
                const rank = idx + 1;
                const score = calculatePresidentScore(entry.score);
                return (
                  <tr
                    key={entry.id}
                    className={`border-b border-matrix-green/10 hover:bg-matrix-green/5 transition-colors cursor-pointer group ${
                      rank <= 3 ? "border-l-2 border-l-neon-yellow/30" : ""
                    }`}
                    tabIndex={0}
                    aria-label={`View profile for ${entry.name}, rank ${rank}`}
                    onClick={() => (window.location.href = `/scorecard?branch=president&id=${entry.id}`)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); window.location.href = `/scorecard?branch=president&id=${entry.id}`; } }}
                  >
                    <td className="px-4 py-3">
                      <span className={`font-bold text-lg ${rankColor(rank)}`}>#{rank}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-white group-hover:text-matrix-green transition-colors">
                          {entry.name}
                        </span>
                        <span className="text-matrix-green/30 text-xs">#{entry.number}</span>
                        {entry.avgApproval == null && entry.gdpGrowthAvg == null && (
                          <span
                            className="text-[9px] font-mono tracking-wide text-amber-400/50 border border-amber-400/20 px-1 shrink-0"
                            title="Score uses historical/expert consensus estimates — live API data not available for this era"
                          >
                            HIST
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-center">
                      <span
                        className={`text-xs px-2 py-0.5 border rounded-sm ${presParty(entry.party).bg} ${presParty(entry.party).color}`}
                      >
                        {presParty(entry.party).label}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-center text-white/50 text-xs">
                      {termYears(entry.termStart, entry.termEnd)}
                      {entry.isCurrent && (
                        <span className="ml-1 text-neon-yellow text-[10px] animate-pulse">ACTIVE</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <ScoreBar score={score} />
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-white/70">
                      {entry.avgApproval != null ? `${entry.avgApproval.toFixed(0)}%` : "—"}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-white/70">
                      {entry.gdpGrowthAvg != null ? `${entry.gdpGrowthAvg.toFixed(1)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Mobile cards */}
        <div className="md:hidden divide-y divide-matrix-green/10">
          {entries.map((entry, idx) => {
            const rank = idx + 1;
            const score = calculatePresidentScore(entry.score);
            return (
              <Link
                key={entry.id}
                href={`/scorecard?branch=president&id=${entry.id}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-matrix-green/5 transition-colors"
              >
                <span className={`text-lg font-bold w-10 shrink-0 ${rankColor(rank)}`}>
                  #{rank}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-white text-sm truncate">{entry.name}</span>
                    <span
                      className={`text-xs px-1 border shrink-0 ${presParty(entry.party).bg} ${presParty(entry.party).color}`}
                    >
                      {presParty(entry.party).label}
                    </span>
                    {entry.avgApproval == null && entry.gdpGrowthAvg == null && (
                      <span className="text-[9px] font-mono text-amber-400/50 border border-amber-400/20 px-1 shrink-0">
                        HIST
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    <ScoreBar score={score} />
                    <span className="text-xs text-white/40">
                      {termYears(entry.termStart, entry.termEnd)}
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </div>

          <div className="mt-4 space-y-1 text-center">
            <p className="text-matrix-green/50 text-xs">
              Higher score = better representation of public mandate. Computed from: independence (15%) +
              follow-through (20%) + public mandate (15%) + effectiveness (20%) +
              competence (15%) + agency alignment (15%). Click any row to view full profile.
            </p>
            <p className="text-matrix-green/30 text-[10px] font-mono">
              <span className="text-amber-400/50 border border-amber-400/20 px-1 mr-1.5">HIST</span>
              = score uses historical/expert consensus estimates; live API data unavailable for that era
            </p>
          </div>
    </>
  );
}

const APPT_PARTY: Record<string, { label: string; color: string; bg: string }> = {
  D:  { label: "D", color: "text-dem-blue",   bg: "bg-dem-blue/20 border-dem-blue/40" },
  R:  { label: "R", color: "text-rep-red",    bg: "bg-rep-red/20 border-rep-red/40" },
};

function apptParty(party: string | null) {
  if (!party) return { label: "—", color: "text-white/50", bg: "bg-white/10 border-white/20" };
  return APPT_PARTY[party] ?? { label: party, color: "text-white/50", bg: "bg-white/10 border-white/20" };
}

function JusticeLeaderboard({
  entries,
  loading,
  error,
}: {
  entries: JusticeLeaderboardEntry[];
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="terminal-window p-8 text-center" role="status" aria-live="polite">
        <p className="text-matrix-green/60 font-mono text-xs tracking-widest animate-pulse">LOADING SCOTUS DATA...</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="terminal-window p-8 text-center border-red-500/40" role="alert">
        <p className="text-red-400">{">"} ERROR: {error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="terminal-window overflow-hidden">
        <TerminalTitlebar title={`scotus_leaderboard.db — ${entries.length} justices`} />

        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm font-terminal">
            <thead>
              <tr className="border-b border-matrix-green/20 text-matrix-green/50 text-xs uppercase tracking-widest">
                <th scope="col" className="px-4 py-3 text-left w-14">RANK</th>
                <th scope="col" className="px-4 py-3 text-left">JUSTICE</th>
                <th scope="col" className="px-3 py-3 text-center w-20">APPT</th>
                <th scope="col" className="px-3 py-3 text-left w-36">SCORE</th>
                <th scope="col" className="px-3 py-3 text-right w-20">CASES</th>
                <th scope="col" className="px-3 py-3 text-right w-24">MAJORITY</th>
                <th scope="col" className="px-3 py-3 text-right w-24">CROSS-BLOC</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, idx) => {
                const rank = idx + 1;
                const score = calculateJusticeScore(entry.score);
                const pp = apptParty(entry.appointingParty);
                return (
                  <tr
                    key={entry.id}
                    className={`border-b border-matrix-green/10 hover:bg-matrix-green/5 transition-colors cursor-pointer group ${
                      rank <= 3 ? "border-l-2 border-l-neon-yellow/30" : ""
                    }`}
                    tabIndex={0}
                    aria-label={`View profile for ${entry.name}, rank ${rank}`}
                    onClick={() => (window.location.href = `/scorecard?branch=scotus&id=${entry.id}`)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); window.location.href = `/scorecard?branch=scotus&id=${entry.id}`; } }}
                  >
                    <td className="px-4 py-3">
                      <span className={`font-bold text-lg ${rankColor(rank)}`}>#{rank}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-white group-hover:text-matrix-green transition-colors">
                          {entry.name}
                        </span>
                        {entry.roleTitle.includes("Chief") && (
                          <span className="text-neon-yellow text-[10px]">CHIEF</span>
                        )}
                        {entry.casesDecided < 100 && (
                          <span
                            className="text-[9px] font-mono tracking-wide text-neon-cyan/40 border border-neon-cyan/20 px-1 shrink-0"
                            title={`Score based on ${entry.casesDecided} cases — may shift significantly as more decisions are issued`}
                          >
                            ~{entry.casesDecided}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-center">
                      <span
                        className={`text-xs px-2 py-0.5 border rounded-sm ${pp.bg} ${pp.color}`}
                      >
                        {pp.label}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <ScoreBar score={score} />
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-white/70">
                      {entry.casesDecided}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums text-white/70">
                      {entry.majorityPct.toFixed(0)}%
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      <span className={entry.crossBlocPct >= 15 ? "text-matrix-green" : entry.crossBlocPct >= 8 ? "text-neon-cyan/70" : "text-white/50"}>
                        {entry.crossBlocPct.toFixed(1)}%
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Mobile cards */}
        <div className="md:hidden divide-y divide-matrix-green/10">
          {entries.map((entry, idx) => {
            const rank = idx + 1;
            const score = calculateJusticeScore(entry.score);
            const pp = apptParty(entry.appointingParty);
            return (
              <Link
                key={entry.id}
                href={`/scorecard?branch=scotus&id=${entry.id}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-matrix-green/5 transition-colors"
              >
                <span className={`text-lg font-bold w-10 shrink-0 ${rankColor(rank)}`}>
                  #{rank}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-white text-sm truncate">{entry.name}</span>
                    <span
                      className={`text-xs px-1 border shrink-0 ${pp.bg} ${pp.color}`}
                    >
                      {pp.label}
                    </span>
                    {entry.casesDecided < 100 && (
                      <span className="text-[9px] font-mono text-neon-cyan/40 border border-neon-cyan/20 px-1 shrink-0">
                        ~{entry.casesDecided}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    <ScoreBar score={score} />
                    <span className="text-xs text-white/40">
                      {entry.casesDecided} cases
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </div>

      <div className="mt-4 space-y-1 text-center">
        <p className="text-matrix-green/50 text-xs">
          Higher score = more impartial jurisprudence. Computed from: ideological consistency (35%) +
          independence (30%) + judicial restraint (20%) + bipartisan agreement (15%).
          Click any row to view full profile.
        </p>
        <p className="text-matrix-green/30 text-[10px] font-mono">
          <span className="text-neon-cyan/40 border border-neon-cyan/20 px-1 mr-1.5">~N</span>
          = fewer than 100 cases decided; score has higher variance and may shift as more decisions are issued
        </p>
      </div>
    </>
  );
}

function LeaderboardContent() {
  const searchParams = useSearchParams();
  const initialBranch = (searchParams.get("branch") as Branch) || "senate";
  const [branch, setBranchState] = useState<Branch>(initialBranch);

  const setBranch = useCallback((b: Branch) => {
    setBranchState(b);
    const url = new URL(window.location.href);
    url.searchParams.set("branch", b);
    window.history.replaceState({}, "", url.toString());
  }, []);

  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [presEntries, setPresEntries] = useState<PresidentLeaderboardEntry[]>([]);
  const [justiceEntries, setJusticeEntries] = useState<JusticeLeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [presLoading, setPresLoading] = useState(false);
  const [justiceLoading, setJusticeLoading] = useState(false);
  const [houseEntries, setHouseEntries] = useState<LeaderboardEntry[]>([]);
  const [houseLoading, setHouseLoading] = useState(false);
  const [housePage, setHousePage] = useState(1);
  const [houseTotalPages, setHouseTotalPages] = useState(1);
  const [houseTotal, setHouseTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [partyFilter, setPartyFilter] = useState<PartyFilter>("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const weights = useScoreWeights();

  useEffect(() => {
    fetchLeaderboard()
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (branch !== "house") return;
    setHouseLoading(true);
    const partyParam = partyFilter !== "ALL" ? partyFilter : undefined;
    fetchRepLeaderboard(housePage, 50, partyParam)
      .then((data) => {
        setHouseEntries(data.entries);
        setHouseTotalPages(data.totalPages);
        setHouseTotal(data.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setHouseLoading(false));
  }, [branch, housePage, partyFilter]);

  useEffect(() => {
    if (branch !== "president") return;
    if (presEntries.length > 0) return;
    setPresLoading(true);
    fetchPresidentLeaderboard()
      .then(setPresEntries)
      .catch((e) => setError(e.message))
      .finally(() => setPresLoading(false));
  }, [branch, presEntries.length]);

  useEffect(() => {
    if (branch !== "scotus") return;
    if (justiceEntries.length > 0) return;
    setJusticeLoading(true);
    fetchJusticeLeaderboard()
      .then(setJusticeEntries)
      .catch((e) => setError(e.message))
      .finally(() => setJusticeLoading(false));
  }, [branch, justiceEntries.length]);

  const activeEntries = branch === "house" ? houseEntries : entries;

  const displayed = useMemo(() => {
    let list = activeEntries;
    if (branch !== "house" && partyFilter !== "ALL") {
      list = list.filter((e) => e.party === partyFilter);
    }

    return [...list].sort((a, b) => {
      if (sortKey === "pac_dollars") return (b.totalFromPacs ?? 0) - (a.totalFromPacs ?? 0);
      if (sortKey === "pac_pct") {
        const pctA = (a.totalRaised ?? 0) > 0 ? (a.totalFromPacs ?? 0) / a.totalRaised : 0;
        const pctB = (b.totalRaised ?? 0) > 0 ? (b.totalFromPacs ?? 0) / b.totalRaised : 0;
        return pctB - pctA;
      }
      return (
        calculateOverallScore(b.representationScore, weights) - calculateOverallScore(a.representationScore, weights)
      );
    });
  }, [activeEntries, branch, partyFilter, sortKey, weights]);

  const counts = useMemo(() => {
    if (branch === "house") {
      return { ALL: houseTotal, D: 0, R: 0, I: 0 };
    }
    return {
      ALL: entries.length,
      D: entries.filter((e) => e.party === "D").length,
      R: entries.filter((e) => e.party === "R").length,
      I: entries.filter((e) => e.party === "I").length,
    };
  }, [branch, entries, houseTotal]);

  return (
    <main id="main-content" tabIndex={-1} className="min-h-screen bg-terminal-bg text-matrix-green font-terminal overflow-x-hidden">
      <MatrixRain />
      <Navbar />

      <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-16">
        {/* Header */}
        <div className="mb-8 text-center">
          <h1
            className="glitch text-3xl sm:text-5xl font-terminal text-matrix-green mb-2 uppercase tracking-widest"
            data-text="LEADERBOARD"
          >
            LEADERBOARD
          </h1>
          <p className="text-matrix-green/50 text-lg">
            {branch === "house" ? "House members" : branch === "president" ? "Presidents" : branch === "scotus" ? "Justices" : "Senators"} ranked by constituent representation score
          </p>
          <div className="ascii-divider mt-4 text-matrix-green/20" aria-hidden="true">
            {"═".repeat(60)}
          </div>
        </div>

        <div className="mb-8">
          <BranchSelector selected={branch} onChange={setBranch} />
        </div>

        <div id={`branch-panel-${branch}`} role="tabpanel" aria-labelledby={`branch-tab-${branch}`} tabIndex={-1}>
        {branch === "president" && <PresidentLeaderboard entries={presEntries} loading={presLoading} error={error} />}

        {branch === "scotus" && <JusticeLeaderboard entries={justiceEntries} loading={justiceLoading} error={error} />}

        {(branch === "senate" || branch === "house") && <>
        {/* Controls */}
        <div className="flex flex-col sm:flex-row gap-4 mb-6 items-start sm:items-center justify-between">
          {/* Party filter */}
          <div className="flex gap-2 flex-wrap" role="group" aria-label="Filter by party">
            {(["ALL", "D", "R", "I"] as PartyFilter[]).map((p) => (
              <button
                key={p}
                aria-pressed={partyFilter === p}
                onClick={() => { setPartyFilter(p); if (branch === "house") setHousePage(1); }}
                className={`px-3 py-1 text-sm border transition-all font-terminal ${
                  partyFilter === p
                    ? p === "D"
                      ? "bg-dem-blue/30 border-dem-blue text-dem-blue"
                      : p === "R"
                        ? "bg-rep-red/30 border-rep-red text-rep-red"
                        : p === "I"
                          ? "bg-ind-purple/30 border-ind-purple text-ind-purple"
                          : "bg-matrix-green/20 border-matrix-green text-matrix-green"
                    : "border-white/10 text-white/40 hover:border-white/30 hover:text-white/60"
                }`}
              >
                {p === "ALL" ? `ALL (${counts.ALL})` : p === "D" ? `DEM (${counts.D})` : p === "R" ? `REP (${counts.R})` : `IND (${counts.I})`}
              </button>
            ))}
          </div>

          {/* Sort */}
          <div className="flex items-center gap-2 text-sm text-matrix-green/60" role="group" aria-label="Sort order">
            <span id="sort-label">SORT:</span>
            {(
              [
                ["score", "INFLUENCE SCORE"],
                ["pac_dollars", "PAC $"],
                ["pac_pct", "PAC %"],
              ] as [SortKey, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setSortKey(key)}
                aria-pressed={sortKey === key}
                className={`px-2 py-0.5 border text-xs transition-all ${
                  sortKey === key
                    ? "border-neon-yellow text-neon-yellow"
                    : "border-white/10 text-white/50 hover:border-white/30 hover:text-white/70"
                }`}
              >
                {label}
                {sortKey === key && <span aria-hidden="true"> ▼</span>}
              </button>
            ))}
          </div>
        </div>

        {/* Loading / Error */}
        {(branch === "senate" ? loading : houseLoading) && (
          <div className="terminal-window p-8 text-center" role="status" aria-live="polite">
            <p className="text-matrix-green/60 font-mono text-xs tracking-widest animate-pulse">
              LOADING {branch === "house" ? "REPRESENTATIVE" : "SENATOR"} DATA...
            </p>
          </div>
        )}
        {error && (
          <div className="terminal-window p-8 text-center border-red-500/40" role="alert">
            <p className="text-red-400">{">"} ERROR: {error}</p>
          </div>
        )}

        {/* Table */}
        {!loading && !error && (
          <div className="terminal-window overflow-hidden">
            <TerminalTitlebar title={`${branch === "house" ? "house" : "senate"}_leaderboard.db — ${branch === "house" ? `${houseTotal} representatives` : `${displayed.length} senators`}`} />

            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-sm font-terminal">
                <thead>
                  <tr className="border-b border-matrix-green/20 text-matrix-green/50 text-xs uppercase tracking-widest">
                    <th scope="col" className="px-4 py-3 text-left w-14">RANK</th>
                    <th scope="col" className="px-4 py-3 text-left">{branch === "house" ? "REPRESENTATIVE" : "SENATOR"}</th>
                    <th scope="col" className="px-3 py-3 text-center w-20">{branch === "house" ? "DIST." : "STATE"}</th>
                    <th scope="col" className="px-3 py-3 text-left w-36">REP. SCORE</th>
                    <th scope="col" className="px-3 py-3 text-center w-16">TREND</th>
                    <th scope="col" className="px-3 py-3 text-right w-24">PAC $</th>
                    <th scope="col" className="px-3 py-3 text-right w-20">PAC %</th>
                    <th scope="col" className="px-3 py-3 text-left w-32">TOP INDUSTRY</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((entry, idx) => {
                    const rankOffset = branch === "house" ? (housePage - 1) * 50 : 0;
                    const rank = rankOffset + idx + 1;
                    const score = calculateOverallScore(entry.representationScore, weights);
                    const pacPct =
                      (entry.totalRaised ?? 0) > 0
                        ? Math.round(((entry.totalFromPacs ?? 0) / entry.totalRaised) * 100)
                        : 0;
                    const isTopTen = rank <= 10;

                    return (
                      <tr
                        key={entry.id}
                        className={`border-b border-matrix-green/10 hover:bg-matrix-green/5 transition-colors cursor-pointer group ${
                          isTopTen ? "border-l-2 border-l-red-500/30" : ""
                        }`}
                        tabIndex={0}
                        aria-label={`View profile for ${entry.name}, ${entry.state}, rank ${rank}, score ${score}`}
                        onClick={() => {
                          const param = branch === "house" ? "representative" : "senator";
                          window.location.href = `/scorecard?branch=${branch}&state=${entry.state}&${param}=${entry.id}`;
                        }}
                        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const param = branch === "house" ? "representative" : "senator"; window.location.href = `/scorecard?branch=${branch}&state=${entry.state}&${param}=${entry.id}`; } }}
                      >
                        <td className="px-4 py-3">
                          <span className={`font-bold text-lg ${rankColor(rank)}`}>
                            #{rank}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-white group-hover:text-matrix-green transition-colors">
                            {entry.name}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span
                            className={`text-xs px-2 py-0.5 border rounded-sm ${partyBg(entry.party)} ${partyColor(entry.party)}`}
                          >
                            {branch === "house" && entry.district != null
                              ? `${entry.state}-${entry.district}`
                              : `${entry.state}-${entry.party}`}
                          </span>
                        </td>
                        <td className="px-3 py-3">
                          <ScoreBar score={score} />
                        </td>
                        <td className="px-3 py-3 text-center">
                          <TrendIndicator trend={entry.trend} />
                        </td>
                        <td className="px-3 py-3 text-right tabular-nums text-white/70">
                          {formatDollars(entry.totalFromPacs)}
                        </td>
                        <td className="px-3 py-3 text-right tabular-nums">
                          <span
                            className={
                              pacPct >= 60
                                ? "text-red-400"
                                : pacPct >= 40
                                  ? "text-orange-400"
                                  : "text-matrix-green/70"
                            }
                          >
                            {pacPct}%
                          </span>
                        </td>
                        <td className="px-3 py-3">
                          <span className="text-neon-cyan/60 text-xs">
                            {entry.topIndustry ?? "—"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div className="md:hidden divide-y divide-matrix-green/10">
              {displayed.map((entry, idx) => {
                const mobileRankOffset = branch === "house" ? (housePage - 1) * 50 : 0;
                const rank = mobileRankOffset + idx + 1;
                const score = calculateOverallScore(entry.representationScore, weights);
                const pacPct =
                  (entry.totalRaised ?? 0) > 0
                    ? Math.round(((entry.totalFromPacs ?? 0) / entry.totalRaised) * 100)
                    : 0;
                return (
                  <Link
                    key={entry.id}
                    href={`/scorecard?branch=${branch}&state=${entry.state}&${branch === "house" ? "representative" : "senator"}=${entry.id}`}
                    className="flex items-center gap-3 px-4 py-3 hover:bg-matrix-green/5 transition-colors"
                  >
                    <span className={`text-lg font-bold w-10 shrink-0 ${rankColor(rank)}`}>
                      #{rank}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-white text-sm truncate">{entry.name}</span>
                        <span
                          className={`text-xs px-1 border shrink-0 ${partyBg(entry.party)} ${partyColor(entry.party)}`}
                        >
                          {branch === "house" && entry.district != null
                            ? `${entry.state}-${entry.district}`
                            : `${entry.state}-${entry.party}`}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 mt-0.5">
                        <ScoreBar score={score} />
                        <TrendIndicator trend={entry.trend} />
                        <span className="text-xs text-white/40">{formatDollars(entry.totalFromPacs)} PAC ({pacPct}%)</span>
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>

            {displayed.length === 0 && !loading && (
              <div className="p-8 text-center text-matrix-green/40">
                <p>{">"} No {branch === "house" ? "representatives" : "senators"} match the current filter.</p>
              </div>
            )}
          </div>
        )}

        {branch === "house" && houseTotalPages > 1 && !houseLoading && (
          <nav className="flex items-center justify-center gap-2 mt-4" aria-label="Leaderboard pagination">
            <button
              onClick={() => setHousePage((p) => Math.max(1, p - 1))}
              disabled={housePage <= 1}
              className="px-3 py-1.5 text-sm border border-matrix-green/30 text-matrix-green hover:bg-matrix-green/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors font-terminal"
              aria-label="Previous page"
            >
              ← PREV
            </button>
            <div className="flex gap-1">
              {Array.from({ length: houseTotalPages }, (_, i) => i + 1).map((p) => (
                <button
                  key={p}
                  onClick={() => setHousePage(p)}
                  aria-current={housePage === p ? "page" : undefined}
                  className={`w-8 h-8 text-sm font-terminal border transition-colors ${
                    housePage === p
                      ? "bg-matrix-green/20 border-matrix-green text-matrix-green"
                      : "border-white/10 text-white/40 hover:border-white/30 hover:text-white/70"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <button
              onClick={() => setHousePage((p) => Math.min(houseTotalPages, p + 1))}
              disabled={housePage >= houseTotalPages}
              className="px-3 py-1.5 text-sm border border-matrix-green/30 text-matrix-green hover:bg-matrix-green/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors font-terminal"
              aria-label="Next page"
            >
              NEXT →
            </button>
          </nav>
        )}

        {/* Footer note */}
        {!loading && !error && displayed.length > 0 && (
          <div className="mt-4 space-y-1 text-center">
            <p className="text-matrix-green/50 text-xs">
              Higher score = better constituent representation. Computed from: funding independence (25%) +
              promise persistence (20%) + independent voting (20%) + funding diversity (15%) +
              legislative effectiveness (20%). Click any row to view full profile.
            </p>
            <p className="text-matrix-green/30 text-[10px] font-mono">
              Scores use Bayesian shrinkage — members with limited public data are pulled toward 50, not penalized or rewarded
            </p>
          </div>
        )}
        </>}
        </div>
      </div>
      <Footer />
      <BackToTop />
    </main>
  );
}

import { Suspense } from "react";

export default function LeaderboardPage() {
  return (
    <Suspense fallback={null}>
      <LeaderboardContent />
    </Suspense>
  );
}
