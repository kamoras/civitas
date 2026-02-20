"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import { fetchLeaderboard } from "@/lib/api";
import { calculateOverallScore, getScoreColor } from "@/lib/corruption";
import type { LeaderboardEntry } from "@/types/senator";

type PartyFilter = "ALL" | "D" | "R" | "I";
type SortKey = "score" | "pac_dollars" | "pac_pct";

function formatDollars(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
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
  // Higher = better (green = good representation, red = captured)
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
      <div className="w-16 h-1.5 bg-white/10 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className={`text-sm font-bold tabular-nums ${getScoreColor(score)}`}>{score}</span>
    </div>
  );
}

export default function LeaderboardPage() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [partyFilter, setPartyFilter] = useState<PartyFilter>("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("score");

  useEffect(() => {
    fetchLeaderboard()
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const displayed = useMemo(() => {
    let list = entries;
    if (partyFilter !== "ALL") list = list.filter((e) => e.party === partyFilter);

    return [...list].sort((a, b) => {
      if (sortKey === "pac_dollars") return b.totalFromPacs - a.totalFromPacs;
      if (sortKey === "pac_pct") {
        const pctA = a.totalRaised > 0 ? a.totalFromPacs / a.totalRaised : 0;
        const pctB = b.totalRaised > 0 ? b.totalFromPacs / b.totalRaised : 0;
        return pctB - pctA;
      }
      return (
        calculateOverallScore(b.representationScore) - calculateOverallScore(a.representationScore)
      );
    });
  }, [entries, partyFilter, sortKey]);

  const counts = useMemo(
    () => ({
      ALL: entries.length,
      D: entries.filter((e) => e.party === "D").length,
      R: entries.filter((e) => e.party === "R").length,
      I: entries.filter((e) => e.party === "I").length,
    }),
    [entries],
  );

  return (
    <main className="min-h-screen bg-terminal-bg text-matrix-green font-terminal overflow-x-hidden">
      <MatrixRain />
      <Navbar />

      <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-16">
        {/* Header */}
        <div className="mb-8 text-center">
          <h1
            className="glitch text-3xl sm:text-5xl font-terminal text-matrix-green mb-2 uppercase tracking-widest"
            data-text="REPRESENTATION SCORECARD"
          >
            REPRESENTATION SCORECARD
          </h1>
          <p className="text-matrix-green/50 text-lg">
            All 100 senators ranked by constituent representation score
          </p>
          <div className="ascii-divider mt-4 text-matrix-green/20">
            {"═".repeat(60)}
          </div>
        </div>

        {/* Controls */}
        <div className="flex flex-col sm:flex-row gap-4 mb-6 items-start sm:items-center justify-between">
          {/* Party filter */}
          <div className="flex gap-2 flex-wrap">
            {(["ALL", "D", "R", "I"] as PartyFilter[]).map((p) => (
              <button
                key={p}
                onClick={() => setPartyFilter(p)}
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
          <div className="flex items-center gap-2 text-sm text-matrix-green/60">
            <span>SORT:</span>
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
                className={`px-2 py-0.5 border text-xs transition-all ${
                  sortKey === key
                    ? "border-neon-yellow text-neon-yellow"
                    : "border-white/10 text-white/30 hover:border-white/30 hover:text-white/50"
                }`}
              >
                {label}
                {sortKey === key && " ▼"}
              </button>
            ))}
          </div>
        </div>

        {/* Loading / Error */}
        {loading && (
          <div className="terminal-window p-8 text-center">
            <p className="text-matrix-green animate-pulse">
              {">"} LOADING SENATOR DATA...
            </p>
          </div>
        )}
        {error && (
          <div className="terminal-window p-8 text-center border-red-500/40">
            <p className="text-red-400">{">"} ERROR: {error}</p>
          </div>
        )}

        {/* Table */}
        {!loading && !error && (
          <div className="terminal-window overflow-hidden">
            <div className="terminal-titlebar">
              <span className="terminal-dot red" />
              <span className="terminal-dot yellow" />
              <span className="terminal-dot green" />
              <span className="ml-3 text-white/40 text-xs font-terminal">
                senate_capture.db — {displayed.length} senators
              </span>
            </div>

            {/* Desktop table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-sm font-terminal">
                <thead>
                  <tr className="border-b border-matrix-green/20 text-matrix-green/50 text-xs uppercase tracking-widest">
                    <th className="px-4 py-3 text-left w-14">RANK</th>
                    <th className="px-4 py-3 text-left">SENATOR</th>
                    <th className="px-3 py-3 text-center w-20">STATE</th>
                    <th className="px-3 py-3 text-left w-36">REP. SCORE</th>
                    <th className="px-3 py-3 text-right w-24">PAC $</th>
                    <th className="px-3 py-3 text-right w-20">PAC %</th>
                    <th className="px-3 py-3 text-left w-32">TOP INDUSTRY</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((entry, idx) => {
                    const rank = idx + 1;
                    const score = calculateOverallScore(entry.representationScore);
                    const pacPct =
                      entry.totalRaised > 0
                        ? Math.round((entry.totalFromPacs / entry.totalRaised) * 100)
                        : 0;
                    const isTopTen = rank <= 10;

                    return (
                      <tr
                        key={entry.id}
                        className={`border-b border-matrix-green/10 hover:bg-matrix-green/5 transition-colors cursor-pointer group ${
                          isTopTen ? "border-l-2 border-l-red-500/30" : ""
                        }`}
                        onClick={() =>
                          (window.location.href = `/senator-scorecard?state=${entry.state}`)
                        }
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
                            {entry.state}-{entry.party}
                          </span>
                        </td>
                        <td className="px-3 py-3">
                          <ScoreBar score={score} />
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
                const rank = idx + 1;
                const score = calculateOverallScore(entry.representationScore);
                const pacPct =
                  entry.totalRaised > 0
                    ? Math.round((entry.totalFromPacs / entry.totalRaised) * 100)
                    : 0;
                return (
                  <Link
                    key={entry.id}
                    href={`/senator-scorecard?state=${entry.state}`}
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
                          {entry.state}-{entry.party}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 mt-0.5">
                        <ScoreBar score={score} />
                        <span className="text-xs text-white/40">{formatDollars(entry.totalFromPacs)} PAC ({pacPct}%)</span>
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>

            {displayed.length === 0 && !loading && (
              <div className="p-8 text-center text-matrix-green/40">
                <p>{">"} No senators match the current filter.</p>
              </div>
            )}
          </div>
        )}

        {/* Footer note */}
        {!loading && !error && entries.length > 0 && (
          <p className="mt-4 text-center text-matrix-green/30 text-xs">
            Higher score = better constituent representation. Computed from: constituent funding (30%) +
            promise fulfillment (30%) + independence from lobbyists (20%) + donor diversity (10%) +
            accountability (10%). Click any row to view full senator profile.
          </p>
        )}
      </div>
    </main>
  );
}
