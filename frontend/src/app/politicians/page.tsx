"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import { fetchPoliticianDirectory } from "@/lib/api";
import type { PoliticianCard } from "@/types/politicians";

type BranchFilter = "all" | "senate" | "house" | "president" | "scotus";
type PartyFilter = "ALL" | "D" | "R" | "I";

const US_STATES = [
  "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
  "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
  "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
  "VA","WA","WV","WI","WY","DC",
];

function partyDot(party: string) {
  const cls =
    party === "D" ? "bg-dem-blue" : party === "R" ? "bg-rep-red" : "bg-ind-purple";
  return <span className={`inline-block w-2 h-2 rounded-full ${cls} mr-1.5`} />;
}

function partyLabel(party: string) {
  if (party === "D") return "Democrat";
  if (party === "R") return "Republican";
  if (party === "I") return "Independent";
  return party;
}

function ScoreBar({ score }: { score: number }) {
  const color =
    score >= 70 ? "bg-matrix-green" :
    score >= 50 ? "bg-cyan-400" :
    score >= 35 ? "bg-yellow-500" :
    "bg-red-500";
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 h-1 bg-matrix-green/10 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${score}%` }} />
      </div>
      <span className="font-mono text-[10px] text-matrix-green/70 w-8 text-right shrink-0">
        {score.toFixed(0)}
      </span>
    </div>
  );
}

function PoliticianCardUI({ p }: { p: PoliticianCard }) {
  const subtitle = [
    p.role,
    p.stateName ?? null,
    p.district != null ? `District ${p.district}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <Link
      href={`/politicians/${p.id}`}
      className="block border border-matrix-green/15 hover:border-matrix-green/40 bg-crt-black/60 hover:bg-crt-black/80 rounded p-4 transition-all group"
    >
      <div className="flex items-start gap-3">
        {p.thumbnailUrl ? (
          <img src={p.thumbnailUrl} alt={p.name} className="w-10 h-10 rounded object-cover shrink-0 opacity-80 group-hover:opacity-100 transition-opacity" />
        ) : (
          <div className="w-10 h-10 rounded border border-matrix-green/20 flex items-center justify-center shrink-0">
            <span className="font-mono text-xs text-matrix-green/40">
              {p.name.split(" ").map(w => w[0]).slice(0, 2).join("")}
            </span>
          </div>
        )}

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            {partyDot(p.party)}
            <span className="font-mono text-sm text-matrix-green group-hover:text-neon-cyan transition-colors truncate">
              {p.name}
            </span>
          </div>
          <p className="font-mono text-[10px] text-matrix-green/40 mb-2 truncate">{subtitle}</p>

          {p.isCurrent === false ? (
            <span className="font-mono text-[9px] text-neon-pink/70 tracking-widest border border-neon-pink/30 px-1.5 py-0.5">
              SEAT VACANT{p.vacancyReason ? ` — ${p.vacancyReason.toUpperCase()}` : ""}
            </span>
          ) : p.hasScorecard && p.overallScore != null ? (
            <ScoreBar score={p.overallScore} />
          ) : (
            <span className="font-mono text-[9px] text-matrix-green/25 tracking-widest">
              SCORECARD PENDING
            </span>
          )}
        </div>

        {p.activeIssueCount > 0 && (
          <div className="shrink-0 flex items-center gap-1 mt-0.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-neon-cyan animate-pulse" />
            <span className="font-mono text-[9px] text-neon-cyan">
              {p.activeIssueCount} ACTIVE
            </span>
          </div>
        )}
      </div>
    </Link>
  );
}

export default function PoliticiansPage() {
  const [branch, setBranch] = useState<BranchFilter>("all");
  const [party, setParty] = useState<PartyFilter>("ALL");
  const [state, setState] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [all, setAll] = useState<PoliticianCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async (b: BranchFilter) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPoliticianDirectory({
        branch: b === "all" ? undefined : b,
      });
      setAll(data);
    } catch {
      setError("Failed to load politicians.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(branch); }, [branch, load]);

  const filtered = useMemo(() => {
    let list = all;
    if (party !== "ALL") list = list.filter(p => p.party === party);
    if (state) list = list.filter(p => p.state === state);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(p => p.name.toLowerCase().includes(q));
    }
    return list;
  }, [all, party, state, search]);

  const showStateFilter = branch === "all" || branch === "senate" || branch === "house";

  const branchTabs: { key: BranchFilter; label: string }[] = [
    { key: "all", label: "ALL" },
    { key: "senate", label: "SENATE" },
    { key: "house", label: "HOUSE" },
    { key: "president", label: "PRESIDENT" },
    { key: "scotus", label: "SCOTUS" },
  ];

  const partyTabs: { key: PartyFilter; label: string }[] = [
    { key: "ALL", label: "ALL" },
    { key: "D", label: "DEM" },
    { key: "R", label: "REP" },
    { key: "I", label: "IND" },
  ];

  const activeCount = filtered.filter(p => p.activeIssueCount > 0).length;

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-7xl mx-auto">

          <div className="text-center mb-8">
            <h1 className="font-pixel text-xl sm:text-3xl text-matrix-green neon-green mb-2">
              POLITICIANS
            </h1>
            <p className="font-mono text-xs text-matrix-green/40">
              CURRENTLY SERVING OFFICIALS · PUBLIC RECORD
            </p>
          </div>

          <TerminalTitlebar title="directory.dat" />
          <div className="border border-t-0 border-matrix-green/20 bg-crt-black/40 p-4 mb-6">

            {/* Branch tabs */}
            <div className="flex flex-wrap gap-2 mb-4">
              {branchTabs.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => { setBranch(key); setState(""); setParty("ALL"); }}
                  className={`font-mono text-[10px] tracking-widest px-3 py-1 border transition-colors ${
                    branch === key
                      ? "border-neon-cyan text-neon-cyan bg-neon-cyan/10"
                      : "border-matrix-green/20 text-matrix-green/40 hover:text-matrix-green hover:border-matrix-green/40"
                  }`}
                >
                  {label}
                </button>
              ))}
              {activeCount > 0 && (
                <span className="font-mono text-[9px] text-neon-cyan/60 self-center ml-2">
                  {activeCount} IN ACTIVE ISSUES
                </span>
              )}
            </div>

            {/* Filters row */}
            <div className="flex flex-wrap gap-3 items-center">
              {/* Search */}
              <input
                ref={searchRef}
                type="text"
                placeholder="SEARCH NAME..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="font-mono text-xs bg-crt-black border border-matrix-green/20 focus:border-matrix-green/60 text-matrix-green placeholder-matrix-green/25 px-3 py-1.5 outline-none w-48"
              />

              {/* Party filter */}
              <div className="flex gap-1">
                {partyTabs.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setParty(key)}
                    className={`font-mono text-[10px] px-2 py-1 border transition-colors ${
                      party === key
                        ? "border-matrix-green/60 text-matrix-green bg-matrix-green/10"
                        : "border-matrix-green/15 text-matrix-green/35 hover:text-matrix-green/60"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* State filter (senate/house/all) */}
              {showStateFilter && (
                <select
                  value={state}
                  onChange={e => setState(e.target.value)}
                  className="font-mono text-[10px] bg-crt-black border border-matrix-green/20 text-matrix-green/60 px-2 py-1 outline-none"
                >
                  <option value="">ALL STATES</option>
                  {US_STATES.map(s => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              )}

              {(search || party !== "ALL" || state) && (
                <button
                  onClick={() => { setSearch(""); setParty("ALL"); setState(""); }}
                  className="font-mono text-[9px] text-matrix-green/30 hover:text-matrix-green/60 transition-colors tracking-widest"
                >
                  CLEAR
                </button>
              )}
            </div>
          </div>

          {/* Results */}
          {loading ? (
            <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest animate-pulse">
              LOADING...
            </div>
          ) : error ? (
            <div className="text-center py-16 font-mono text-xs text-red-400/60">{error}</div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest">
              NO RESULTS
            </div>
          ) : (
            <>
              <p className="font-mono text-[10px] text-matrix-green/30 mb-3 tracking-widest">
                {filtered.length} POLITICIAN{filtered.length !== 1 ? "S" : ""}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {filtered.map(p => (
                  <PoliticianCardUI key={p.id} p={p} />
                ))}
              </div>
            </>
          )}

        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
