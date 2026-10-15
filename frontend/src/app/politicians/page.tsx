"use client";

import { displayScore } from "@/lib/formatting";
import {
  districtName,
  matchesDistrictQuery,
  placeDistrictSummary,
  stateBallotHref,
} from "@/lib/elections";
import { Suspense, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import PageMasthead from "@/components/layout/PageMasthead";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import PageFallback from "@/components/layout/PageFallback";
import { countyFinderStatus, countyNames, finderSuggestions } from "@/lib/countyFinder";
import { fetchHouseDistrictsByCounty, fetchPoliticianDirectory } from "@/lib/api";
import { useAsyncData } from "@/hooks/useAsyncData";
import { getScoreBgColor } from "@/lib/representation";
import { formerOfficeBadge } from "@/lib/officeStatus";
import { delegationOrder } from "@/lib/delegationOrder";
import type { HouseDistrictsByCounty, PoliticianCard } from "@/types/politicians";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import Photo from "@/components/Photo";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";

type BranchFilter = "all" | "senate" | "house" | "president" | "scotus";
type PartyFilter = "ALL" | "D" | "R" | "I";

// By full name, as a reader looks for their state: a list of postal codes
// asks them to recall which of MI, MN, MS and MO is theirs.
const STATES_BY_NAME = [...STATE_CODES].sort((a, b) =>
  STATE_NAMES[a].localeCompare(STATE_NAMES[b])
);

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

function Initials({ name }: { name: string }) {
  return (
    <div className="w-10 h-10 border border-white/[0.07] flex items-center justify-center shrink-0">
      <span className="font-mono text-xs text-ink-min">
        {name
          .split(" ")
          .map((w) => w[0])
          .slice(0, 2)
          .join("")}
      </span>
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
          <Photo
            src={p.thumbnailUrl}
            // Hundreds of cards: Photo's lazy loads each portrait as it
            // scrolls into view (loading="lazy", decoding="async").
            lazy
            alt={p.name}
            sizes="40px"
            className="w-10 h-10 object-cover object-[center_25%] shrink-0 opacity-80 group-hover:opacity-100 transition-opacity"
            fallback={<Initials name={p.name} />}
          />
        ) : (
          <Initials name={p.name} />
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

/** Keeps ?state= and ?county= in the address bar so a filtered delegation
 * can be bookmarked, shared and returned to with Back. The History API, not
 * router.replace(): on this statically prerendered route a router.replace()
 * after a load with a query string silently does nothing (AGENTS.md,
 * "Client-side URL state"). */
function writeParams(params: Record<string, string>) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(params)) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}

/** Find your House member by a city, town or county you know. Civitas
 *  never asks where a visitor lives (AGENTS.md section 8): this filters the
 *  delegation over the counties and places each district covers, a list
 *  the page already has.
 *
 *  These are the lines of the Congress in office — the districts the
 *  members now serving were elected in. The state ballot page uses the
 *  coming election's lines, which in a state that redrew are different
 *  districts under the same numbers, so each surface names its map. */
function CountyFinder({
  stateCode,
  lines,
  query,
  onQuery,
  matches,
}: {
  stateCode: string;
  lines: HouseDistrictsByCounty;
  query: string;
  onQuery: (q: string) => void;
  matches: { district: number; counties: string[] }[];
}) {
  const id = useId();
  const names = countyNames(lines);
  const status = countyFinderStatus(
    names,
    query,
    matches.length,
    placeDistrictSummary(lines.districts, query)
  );
  return (
    <div className="mb-4 border border-white/[0.07] p-4">
      <label htmlFor={id} className="block text-sm font-bold text-ink-hi">
        Find your House member by city, town or county
      </label>
      <p className="mt-1 text-xs text-ink-lo">
        Districts as of the current Congress: the ones today&apos;s members were elected in. Nothing
        you type leaves this page.
      </p>
      <input
        id={id}
        type="search"
        list={`${id}-counties`}
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder="City, town or county"
        autoComplete="off"
        className="mt-2 w-full max-w-xs border border-white/15 bg-surface-base px-3 py-2 font-mono text-sm text-ink-hi placeholder:text-ink-min outline-none focus:border-phos/40"
      />
      <datalist id={`${id}-counties`}>
        {finderSuggestions(lines).map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      {status && (
        <p className="mt-2 text-sm text-ink-lo" role="status">
          {status}
        </p>
      )}
      <p className="mt-2 text-xs text-ink-min">
        {/* No cycle year: it would go stale when the ballot page moves on. */}
        Voting in the coming election?{" "}
        <Link
          href={`${stateBallotHref(stateCode)}#ballot-house`}
          className="underline underline-offset-2 hover:text-phos"
        >
          Find your election district on the {STATE_NAMES[stateCode] ?? stateCode} ballot page
        </Link>
        . Its lines can differ from the ones your current member was elected on.
      </p>
    </div>
  );
}

export default function PoliticiansPage() {
  return (
    // The frame and a skeleton in the first paint: the content reads the
    // query string, which the server does not have (PageFallback).
    <Suspense
      fallback={
        <PageFallback
          eyebrow="Directory · currently serving officials"
          title="Politicians"
          rows={6}
        />
      }
    >
      <PoliticiansPageContent />
    </Suspense>
  );
}

function PoliticiansPageContent() {
  const searchParams = useSearchParams();
  const initialBranch = (searchParams.get("branch") as BranchFilter) || "all";
  const initialState = searchParams.get("state") || "";
  // Latched at mount (AGENTS.md "Client-side URL state"): writing ?county=
  // brings it straight back through useSearchParams.
  const [county, setCountyValue] = useState<string>(() => searchParams.get("county") || "");
  const setCounty = (next: string) => {
    setCountyValue(next);
    writeParams({ county: next.trim() });
  };
  const [branch, setBranch] = useState<BranchFilter>(initialBranch);
  const [party, setParty] = useState<PartyFilter>("ALL");
  const [state, setStateValue] = useState<string>(initialState);
  const setState = (next: string) => {
    setStateValue(next);
    setCountyValue("");
    writeParams({ state: next, county: "" });
  };
  const [search, setSearch] = useState<string>("");
  const searchRef = useRef<HTMLInputElement>(null);

  // Party, state and name are applied client-side over one branch fetch (see
  // `filtered` below), so the branch is the whole request identity.
  const directory = useAsyncData(`politicians:${branch}`, () =>
    fetchPoliticianDirectory({ branch: branch === "all" ? undefined : branch })
  );
  const all = directory.data ?? EMPTY_DIRECTORY;

  const showCountyFinder =
    !!state &&
    state !== "DC" &&
    branch !== "senate" &&
    branch !== "president" &&
    branch !== "scotus";
  const houseLines = useAsyncData(
    `house-districts:${state}`,
    showCountyFinder ? () => fetchHouseDistrictsByCounty(state) : null
  ).data;
  // A one-seat state has no district to find.
  const linesReady = showCountyFinder && !!houseLines?.available && houseLines.districts.length > 1;
  const countyMatches = useMemo(
    () =>
      linesReady && county.trim()
        ? houseLines!.districts.filter((d) =>
            matchesDistrictQuery(
              { district: null, areas: [...d.counties, ...(d.places ?? [])], candidates: [] },
              county
            )
          )
        : [],
    [linesReady, houseLines, county]
  );
  const loading = directory.loading;
  const error = directory.error ? "Failed to load politicians." : null;

  const filtered = useMemo(() => {
    let list = all;
    if (party !== "ALL") list = list.filter((p) => p.party === party);
    if (state) list = delegationOrder(list.filter((p) => p.state === state));
    // A county narrows the House members; senators represent all of it.
    if (linesReady && county.trim()) {
      const districts = new Set(countyMatches.map((d) => d.district));
      list = list.filter((p) => p.branch !== "house" || districts.has(p.district ?? -1));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) => p.name.toLowerCase().includes(q));
    }
    return list;
  }, [all, party, state, search, linesReady, county, countyMatches]);

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
                  {STATES_BY_NAME.map((s) => (
                    <option key={s} value={s}>
                      {STATE_NAMES[s]}
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

          {linesReady && (
            <CountyFinder
              stateCode={state}
              lines={houseLines!}
              query={county}
              onQuery={setCounty}
              matches={countyMatches}
            />
          )}

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
