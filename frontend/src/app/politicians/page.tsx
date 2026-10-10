"use client";

import { displayScore } from "@/lib/formatting";
import { districtName, stateBallotHref } from "@/lib/elections";
import { Suspense, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import { fetchPoliticianDirectory } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { getScoreBgColor } from "@/lib/representation";
import { formerOfficeBadge } from "@/lib/officeStatus";
import { delegationOrder } from "@/lib/delegationOrder";
import type { PoliticianCard } from "@/types/politicians";
import { BOXED_CONTROL } from "@/lib/controlStyles";

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

function ScoreBar({ score: raw }: { score: number }) {
  const score = displayScore(raw);
  const color = getScoreBgColor(score);
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 h-1 bg-white/[0.03] overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${score}%` }} />
      </div>
      <span className="font-mono text-xs text-ink w-8 text-right shrink-0">{score}</span>
    </div>
  );
}

function PoliticianCardUI({ p }: { p: PoliticianCard }) {
  const subtitle = [
    p.role,
    p.stateName ?? null,
    p.district != null ? districtName(p.district) : null,
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
            // Hundreds of cards: load the portraits as they scroll into view.
            loading="lazy"
            decoding="async"
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
          ) : p.branch === "scotus" ? (
            // Justice v3: justices are not scored, not "pending".
            <span className="font-mono text-xs text-ink-min tracking-widest">NOT SCORED</span>
          ) : (
            <span className="font-mono text-xs text-ink-min tracking-widest">
              SCORECARD PENDING
            </span>
          )}
        </div>

        {p.activeIssueCount > 0 && (
          <div className="shrink-0 flex items-center gap-1 mt-0.5">
            <span className="inline-block w-1.5 h-1.5 bg-signal-cyan animate-pulse" />
            <span className="font-mono text-xs text-signal-cyan">
              IN {p.activeIssueCount} {p.activeIssueCount === 1 ? "ISSUE" : "ISSUES"}
            </span>
          </div>
        )}
      </div>
    </Link>
  );
}

const EMPTY_DIRECTORY: PoliticianCard[] = [];

/** Keeps ?state= in the address bar so a filtered delegation can be
 * bookmarked, shared and returned to with Back. The History API, not
 * router.replace(): on this statically prerendered route a router.replace()
 * after a load with a query string silently does nothing (AGENTS.md,
 * "Client-side URL state"). */
function writeStateParam(state: string) {
  const url = new URL(window.location.href);
  if (state) url.searchParams.set("state", state);
  else url.searchParams.delete("state");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
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
  const [state, setStateValue] = useState<string>(initialState);
  const setState = (next: string) => {
    setStateValue(next);
    writeStateParam(next);
  };
  const [search, setSearch] = useState<string>("");
  const searchRef = useRef<HTMLInputElement>(null);

  // Party, state and name are applied client-side over one branch fetch (see
  // `filtered` below), so the branch is the whole request identity.
  const directory = useAsyncData(`politicians:${branch}`, () =>
    fetchPoliticianDirectory({ branch: branch === "all" ? undefined : branch })
  );
  const all = directory.data ?? EMPTY_DIRECTORY;
  const loading = directory.loading;
  const error = directory.error ? "Failed to load politicians." : null;

  const filtered = useMemo(() => {
    let list = all;
    if (party !== "ALL") list = list.filter((p) => p.party === party);
    if (state) list = delegationOrder(list.filter((p) => p.state === state));
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
          <PageMasthead
            className="mb-8"
            eyebrow="Directory · currently serving officials"
            title="Politicians"
          >
            <p>Everyone currently holding federal office, as recorded in the public register.</p>
          </PageMasthead>

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
                  aria-pressed={branch === key}
                  className={`font-mono text-xs tracking-widest px-3 py-1 border transition-colors ${
                    branch === key ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                  }`}
                >
                  {label}
                </button>
              ))}
              {activeCount > 0 && (
                <span className="font-mono text-xs text-ink-lo self-center ml-2">
                  {activeCount} IN ACTION CENTER ISSUES
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
                aria-label="Search politicians by name"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="font-mono text-xs bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder:text-ink-min px-3 py-1.5 outline-none w-48"
              />

              {/* Party filter */}
              <div className="flex gap-1">
                {partyTabs.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setParty(key)}
                    aria-pressed={party === key}
                    className={`font-mono text-xs px-2 py-1 border transition-colors ${
                      party === key ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
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
              {/* A House district is a number few people know. The state's
                  ballot page finds it from the county a reader lives in, with
                  nothing typed or sent (AGENTS.md section 8). Those are the
                  lines of the coming election, which in a redrawn state are
                  not the ones the sitting member was elected on, so the
                  link says which lines it shows. */}
              {state && state !== "DC" && branch !== "senate" && (
                <p className="mb-4 text-sm text-ink-lo">
                  Don&apos;t know your House district?{" "}
                  <Link
                    href={`${stateBallotHref(state)}#ballot-house`}
                    className="underline underline-offset-2 hover:text-phos"
                  >
                    Find it by county on the {state} ballot page
                  </Link>{" "}
                  (2026 district lines).
                </p>
              )}
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
