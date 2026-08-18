"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import { fetchPoliticianDirectory } from "@/lib/api";
import { getScoreBgColor } from "@/lib/representation";
import { formerOfficeBadge } from "@/lib/officeStatus";
import type { PoliticianCard } from "@/types/politicians";

type BranchFilter = "all" | "senate" | "house" | "president" | "scotus";
type PartyFilter = "ALL" | "D" | "R" | "I";

const US_STATES = [
  "AL",
  "AK",
  "AZ",
  "AR",
  "CA",
  "CO",
  "CT",
  "DE",
  "FL",
  "GA",
  "HI",
  "ID",
  "IL",
  "IN",
  "IA",
  "KS",
  "KY",
  "LA",
  "ME",
  "MD",
  "MA",
  "MI",
  "MN",
  "MS",
  "MO",
  "MT",
  "NE",
  "NV",
  "NH",
  "NJ",
  "NM",
  "NY",
  "NC",
  "ND",
  "OH",
  "OK",
  "OR",
  "PA",
  "RI",
  "SC",
  "SD",
  "TN",
  "TX",
  "UT",
  "VT",
  "VA",
  "WA",
  "WV",
  "WI",
  "WY",
  "DC",
];

function partyDot(party: string) {
  const cls = party === "D" ? "bg-dem-blue" : party === "R" ? "bg-signal-red" : "bg-ind-purple";
  return <span className={`inline-block w-2 h-2 ${cls} mr-1.5`} />;
}

function ScoreBar({ score }: { score: number }) {
  const color = getScoreBgColor(score);
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 h-1 bg-white/[0.03] overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="font-mono text-xs text-ink w-8 text-right shrink-0">{score.toFixed(0)}</span>
    </div>
  );
}

function PoliticianCardUI({ p }: { p: PoliticianCard }) {
  const subtitle = [
    p.role,
    p.stateName ?? null,
    p.district != null ? `District ${p.district}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Link
      href={`/politicians/${p.id}`}
      className="block border border-white/[0.07] hover:border-white/15 bg-surface-base hover:bg-surface-base p-4 transition-all group"
    >
      <div className="flex items-start gap-3">
        {p.thumbnailUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- external, varied politician-photo hosts; not worth per-host next/image remotePatterns
          <img
            src={p.thumbnailUrl}
            alt={p.name}
            className="w-10 h-10 object-cover shrink-0 opacity-80 group-hover:opacity-100 transition-opacity"
          />
        ) : (
          <div className="w-10 h-10 border border-white/[0.07] flex items-center justify-center shrink-0">
            <span className="font-mono text-xs text-ink-min">
              {p.name
                .split(" ")
                .map((w) => w[0])
                .slice(0, 2)
                .join("")}
            </span>
          </div>
        )}

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            {partyDot(p.party)}
            <span className="font-mono text-sm text-ink-hi group-hover:text-phos transition-colors truncate">
              {p.name}
            </span>
          </div>
          <p className="font-mono text-xs text-ink-min mb-2 truncate">{subtitle}</p>

          {p.leadershipTitle && (
            <span className="inline-block font-mono text-xs text-signal-amber tracking-widest border border-signal-amber/40 px-1.5 py-0.5 mb-2">
              {p.leadershipTitle.toUpperCase()}
            </span>
          )}

          {p.isCurrent === false ? (
            <span className="font-mono text-xs text-ink-lo tracking-widest border border-signal-magenta/40 px-1.5 py-0.5">
              {formerOfficeBadge(p)}
            </span>
          ) : p.hasScorecard && p.overallScore != null ? (
            <ScoreBar score={p.overallScore} />
          ) : (
            <span className="font-mono text-xs text-ink-min tracking-widest">
              SCORECARD PENDING
            </span>
          )}
        </div>

        {p.activeIssueCount > 0 && (
          <div className="shrink-0 flex items-center gap-1 mt-0.5">
            <span className="inline-block w-1.5 h-1.5 bg-signal-cyan animate-pulse" />
            <span className="font-mono text-xs text-signal-cyan">{p.activeIssueCount} ACTIVE</span>
          </div>
        )}
      </div>
    </Link>
  );
}

export default function PoliticiansPage() {
  return (
    <Suspense fallback={null}>
      <PoliticiansPageContent />
    </Suspense>
  );
}

function PoliticiansPageContent() {
  const searchParams = useSearchParams();
  const initialBranch = (searchParams.get("branch") as BranchFilter) || "all";
  const initialState = searchParams.get("state") || "";
  const [branch, setBranch] = useState<BranchFilter>(initialBranch);
  const [party, setParty] = useState<PartyFilter>("ALL");
  const [state, setState] = useState<string>(initialState);
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

  useEffect(() => {
    load(branch);
  }, [branch, load]);

  const filtered = useMemo(() => {
    let list = all;
    if (party !== "ALL") list = list.filter((p) => p.party === party);
    if (state) list = list.filter((p) => p.state === state);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) => p.name.toLowerCase().includes(q));
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

  const activeCount = filtered.filter((p) => p.activeIssueCount > 0).length;

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-7xl mx-auto">
          <div className="mb-8 border-b-3 border-phos pb-5">
            <h1 className="font-display font-semibold text-xl sm:text-3xl text-ink-hi mb-2">
              POLITICIANS
            </h1>
            <p className="font-mono text-xs text-ink-min">
              CURRENTLY SERVING OFFICIALS · PUBLIC RECORD
            </p>
          </div>

          <TerminalTitlebar title="Directory" />
          <div className="border border-t-0 border-white/[0.07] bg-surface-base p-4 mb-6">
            {/* Branch tabs */}
            <div className="flex flex-wrap gap-2 mb-4">
              {branchTabs.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => {
                    setBranch(key);
                    setState("");
                    setParty("ALL");
                  }}
                  className={`font-mono text-xs tracking-widest px-3 py-1 border transition-colors ${
                    branch === key
                      ? "border-signal-cyan/40 text-signal-cyan bg-signal-cyan/10"
                      : "border-white/[0.07] text-ink-min hover:text-phos hover:border-white/15"
                  }`}
                >
                  {label}
                </button>
              ))}
              {activeCount > 0 && (
                <span className="font-mono text-xs text-ink-lo self-center ml-2">
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
                onChange={(e) => setSearch(e.target.value)}
                className="font-mono text-xs bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder-white/15 px-3 py-1.5 outline-none w-48"
              />

              {/* Party filter */}
              <div className="flex gap-1">
                {partyTabs.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setParty(key)}
                    className={`font-mono text-xs px-2 py-1 border transition-colors ${
                      party === key
                        ? "border-phos/40 text-ink-hi bg-white/[0.03]"
                        : "border-white/[0.07] text-ink-min hover:text-phos"
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
                  onChange={(e) => setState(e.target.value)}
                  aria-label="Filter by state"
                  className="font-mono text-xs bg-surface-base border border-white/[0.07] text-ink-lo px-2 py-1 outline-none"
                >
                  <option value="">ALL STATES</option>
                  {US_STATES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              )}

              {(search || party !== "ALL" || state) && (
                <button
                  onClick={() => {
                    setSearch("");
                    setParty("ALL");
                    setState("");
                  }}
                  className="font-mono text-xs text-ink-min hover:text-phos transition-colors tracking-widest"
                >
                  CLEAR
                </button>
              )}
            </div>
          </div>

          {/* Results */}
          {loading ? (
            <div className="text-center py-16 font-mono text-xs text-ink-min tracking-widest animate-pulse">
              LOADING...
            </div>
          ) : error ? (
            <div className="text-center py-16 font-mono text-xs text-signal-red">{error}</div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 font-mono text-xs text-ink-min tracking-widest">
              NO RESULTS
            </div>
          ) : (
            <>
              <p className="font-mono text-xs text-ink-min mb-3 tracking-widest">
                {filtered.length} POLITICIAN{filtered.length !== 1 ? "S" : ""}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {filtered.map((p) => (
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
