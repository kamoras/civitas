"use client";

import {
  districtName,
  filterDistricts,
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
import { formerOfficeBadge } from "@/lib/officeStatus";
import { delegationOrder } from "@/lib/delegationOrder";
import type { HouseDistrictsByCounty, PoliticianCard } from "@/types/politicians";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import Photo from "@/components/Photo";
import { STATE_CODES, STATE_NAMES } from "@/lib/stateCodes";
import SectionTabs from "@/components/layout/SectionTabs";
import { useYourState } from "@/hooks/useYourState";
import { useYourDistrict } from "@/hooks/useYourDistrict";
import DistrictLocator from "@/components/districts/DistrictLocator";

type BranchFilter = "all" | "senate" | "house";
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
      <div className="flex flex-wrap items-start gap-3">
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

        <div className="min-w-0 flex-[1_1_10rem]">
          <div className="flex items-center gap-2 mb-0.5">
            {partyDot(p.party)}
            <span className="font-mono text-sm text-ink-hi group-hover:text-phos transition-colors truncate">
              {p.name}
            </span>
          </div>
          <p className="font-mono text-xs text-ink-min mb-2 truncate">{subtitle}</p>

          {p.leadershipTitle && (
            <span className="inline-block max-w-full font-mono text-xs text-signal-amber border border-signal-amber/40 px-1.5 py-0.5 mb-2">
              {p.leadershipTitle}
            </span>
          )}

          {p.isCurrent === false ? (
            <span className="font-mono text-xs text-ink-lo border border-signal-magenta/40 px-1.5 py-0.5">
              {formerOfficeBadge(p)}
            </span>
          ) : null}
        </div>

        {p.activeIssueCount > 0 && (
          <div className="shrink-0 flex items-center gap-1 mt-0.5">
            <span className="inline-block w-1.5 h-1.5 bg-signal-cyan animate-pulse" />
            <span className="font-mono text-xs text-signal-cyan">
              In {p.activeIssueCount} {p.activeIssueCount === 1 ? "issue" : "issues"}
            </span>
          </div>
        )}
      </div>
    </Link>
  );
}

const EMPTY_DIRECTORY: PoliticianCard[] = [];
const CARD_GRID = "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3";

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
  picked,
  onPick,
  onOtherState,
}: {
  stateCode: string;
  lines: HouseDistrictsByCounty;
  query: string;
  onQuery: (q: string) => void;
  matches: { district: number; counties: string[] }[];
  /** The district settled by location or the town's map, if any. */
  picked: number | null;
  onPick: (state: string, district: number | null) => void;
  onOtherState: (state: string) => void;
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
      <label htmlFor={id} className="block text-sm font-semibold text-ink-hi">
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
      {picked != null && (
        <p className="mt-2 text-sm text-ink-hi">
          Showing {districtName(picked)}.{" "}
          <button
            type="button"
            onClick={() => onPick(stateCode, null)}
            className="min-h-6 underline underline-offset-2 hover:text-phos"
          >
            Show every district
          </button>
        </p>
      )}
      <DistrictLocator
        state={stateCode}
        lines={`congress-${lines.congress}`}
        linesLabel="the lines today's members were elected on"
        districts={lines.districts}
        query={query}
        onFound={(found) => onPick(found.state, found.house)}
        onOtherState={onOtherState}
      />
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

// The masthead's line, sent with the server's fallback too (PageFallback).
const MEMBERS_EYEBROW = "Congress · find your members";
const MEMBERS_TITLE = "Members of Congress";
const MEMBERS_LEDE = (
  <p>
    Every serving senator and representative. Pick a state for its two senators and its House
    members, then a city, town or county to find the district.
  </p>
);

/** Only the chambers: a president or justice isn't anyone's member by
 *  state (they have their own pages), and `?branch=` written by the old
 *  directory for them never reaches here (next.config.mjs). */
function branchParam(value: string | null): BranchFilter {
  return value === "senate" || value === "house" ? value : "all";
}

/** The find-your-members directory (`/congress/members`): Senate and House,
 *  filtered by state, town or county, party and name. */
export default function MembersDirectory() {
  return (
    // The frame and a skeleton in the first paint: the content reads the
    // query string, which the server does not have (PageFallback).
    <Suspense
      fallback={
        <PageFallback
          eyebrow={MEMBERS_EYEBROW}
          title={MEMBERS_TITLE}
          lede={MEMBERS_LEDE}
          width="max-w-7xl"
          mastheadClassName="mb-8"
          rows={6}
        />
      }
    >
      <MembersDirectoryContent />
    </Suspense>
  );
}

function MembersDirectoryContent() {
  const searchParams = useSearchParams();
  const initialBranch = branchParam(searchParams.get("branch"));
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
  // The filter starts from ?state=, else from the state chosen in the
  // header for this tab (useYourState), and follows the header until the
  // reader sets the filter here; a state picked here becomes the header's.
  const yours = useYourState();
  const [stateValue, setStateValue] = useState<string>(initialState);
  const [stateTouched, setStateTouched] = useState(Boolean(initialState));
  const state = stateTouched ? stateValue : yours.state;
  const setState = (next: string) => {
    setStateTouched(true);
    setStateValue(next);
    setCountyValue("");
    writeParams({ state: next, county: "" });
    if (next) yours.setState(next);
  };
  // The district found by location or a town's map, kept for this tab
  // (useYourDistrict); it applies only to its own state and map.
  const yourDistrict = useYourDistrict();
  const [search, setSearch] = useState<string>("");
  const searchRef = useRef<HTMLInputElement>(null);

  // Party, state and name are applied client-side over one branch fetch (see
  // `filtered` below), so the branch is the whole request identity.
  const directory = useAsyncData(`politicians:${branch}`, () =>
    fetchPoliticianDirectory({ branch: branch === "all" ? undefined : branch })
  );
  // Both chambers: the unfiltered directory also lists the president and
  // the justices, who have pages of their own.
  const all = useMemo(
    () =>
      (directory.data ?? EMPTY_DIRECTORY).filter(
        (p) => p.branch === "senate" || p.branch === "house"
      ),
    [directory.data]
  );

  const showCountyFinder = !!state && state !== "DC" && branch !== "senate";
  const houseLines = useAsyncData(
    `house-districts:${state}`,
    showCountyFinder ? () => fetchHouseDistrictsByCounty(state) : null
  ).data;
  // A one-seat state has no district to find.
  const linesReady = showCountyFinder && !!houseLines?.available && houseLines.districts.length > 1;
  const countyMatches = useMemo(
    () =>
      linesReady && county.trim()
        ? filterDistricts(houseLines!.districts, county, (d) => ({
            district: null,
            areas: [...d.counties, ...(d.places ?? [])],
            candidates: [],
          }))
        : [],
    [linesReady, houseLines, county]
  );
  const linesName = houseLines ? `congress-${houseLines.congress}` : "";
  const picked =
    linesReady &&
    yourDistrict.district?.state === state &&
    yourDistrict.district.lines === linesName
      ? yourDistrict.district.house
      : null;
  const loading = directory.loading;
  const error = directory.error ? "Failed to load the members." : null;

  const filtered = useMemo(() => {
    let list = all;
    if (party !== "ALL") list = list.filter((p) => p.party === party);
    if (state) list = delegationOrder(list.filter((p) => p.state === state));
    // A district found by location or a town's map settles the House
    // member; a county narrows them; senators represent all of it.
    if (picked != null) {
      list = list.filter((p) => p.branch !== "house" || p.district === picked);
    } else if (linesReady && county.trim()) {
      const districts = new Set(countyMatches.map((d) => d.district));
      list = list.filter((p) => p.branch !== "house" || districts.has(p.district ?? -1));
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) => p.name.toLowerCase().includes(q));
    }
    return list;
  }, [all, party, state, search, linesReady, county, countyMatches, picked]);

  const branchTabs: { key: BranchFilter; label: string }[] = [
    { key: "all", label: "Both chambers" },
    { key: "senate", label: "Senate" },
    { key: "house", label: "House" },
  ];

  const partyTabs: { key: PartyFilter; label: string }[] = [
    { key: "ALL", label: "All" },
    { key: "D", label: "DEM" },
    { key: "R", label: "REP" },
    { key: "I", label: "IND" },
  ];

  const activeCount = filtered.filter((p) => p.activeIssueCount > 0).length;

  // A picked state's senators come before the House finder: they are the
  // answer to "who represents me" for everyone in the state, while the
  // finder is the question only the House part needs. With the finder
  // first, the senators of an arrival from the homepage's state picker sat
  // just below a phone's first screen.
  const senators = state ? filtered.filter((p) => p.branch === "senate") : [];
  const rest = state ? filtered.filter((p) => p.branch !== "senate") : filtered;
  const finder = linesReady && (
    <CountyFinder
      stateCode={state}
      lines={houseLines!}
      query={county}
      onQuery={(q) => {
        setCounty(q);
        if (picked != null) yourDistrict.setDistrict(null);
      }}
      matches={countyMatches}
      picked={picked}
      onPick={(st, house) =>
        yourDistrict.setDistrict(house == null ? null : { state: st, lines: linesName, house })
      }
      onOtherState={setState}
    />
  );

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-7xl mx-auto">
          <SectionTabs section="congress" active="members" />
          <PageMasthead className="mb-8" eyebrow={MEMBERS_EYEBROW} title={MEMBERS_TITLE}>
            {MEMBERS_LEDE}
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
                    setParty("ALL");
                    writeParams({ branch: key === "all" ? "" : key });
                  }}
                  aria-pressed={branch === key}
                  className={`font-mono text-xs px-3 py-1 border transition-colors ${
                    branch === key ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                  }`}
                >
                  {label}
                </button>
              ))}
              {activeCount > 0 && (
                <span className="font-mono text-xs text-ink-lo self-center ml-2">
                  {activeCount} in action center issues
                </span>
              )}
            </div>

            {/* Filters row */}
            <div className="flex flex-wrap gap-3 items-center">
              {/* Search */}
              <input
                ref={searchRef}
                type="text"
                placeholder="Search name..."
                aria-label="Search members by name"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="font-mono text-xs bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder:text-ink-min px-3 py-1.5 outline-none w-48 max-w-full"
              />

              {/* Party filter */}
              <div className="flex flex-wrap gap-1">
                {partyTabs.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setParty(key)}
                    aria-pressed={party === key}
                    className={`inline-flex min-h-6 min-w-6 items-center justify-center font-mono text-xs px-2 py-1 border transition-colors ${
                      party === key ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* State filter (senate/house/all) */}
              <select
                value={state}
                onChange={(e) => setState(e.target.value)}
                aria-label="Filter by state"
                className="font-mono text-xs bg-surface-base border border-white/[0.07] text-ink-lo px-2 py-1 outline-none"
              >
                <option value="">All states</option>
                {STATES_BY_NAME.map((s) => (
                  <option key={s} value={s}>
                    {STATE_NAMES[s]}
                  </option>
                ))}
              </select>

              {(search || party !== "ALL" || state) && (
                <button
                  onClick={() => {
                    setSearch("");
                    setParty("ALL");
                    setState("");
                  }}
                  className="font-mono text-xs text-ink-min hover:text-phos transition-colors"
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          {/* Results */}
          {loading ? (
            <div className="text-center py-16 font-mono text-xs text-ink-min pulse-subtle">
              Loading...
            </div>
          ) : error ? (
            <>
              {finder}
              <div className="text-center py-16 font-mono text-xs text-signal-red">{error}</div>
            </>
          ) : filtered.length === 0 ? (
            <>
              {finder}
              <div className="text-center py-16 font-mono text-xs text-ink-min">No results</div>
            </>
          ) : (
            <>
              <p className="font-mono text-xs text-ink-min mb-3">
                {filtered.length} member{filtered.length !== 1 ? "s" : ""}
              </p>
              {senators.length > 0 && (
                <div className={`${CARD_GRID} mb-4`}>
                  {senators.map((p) => (
                    <PoliticianCardUI key={p.id} p={p} />
                  ))}
                </div>
              )}
              {finder}
              <div className={CARD_GRID}>
                {rest.map((p) => (
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
