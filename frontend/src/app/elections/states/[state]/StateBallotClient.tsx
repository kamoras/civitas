"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import CoverageFeed, { useMounted } from "@/components/elections/CoverageFeed";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import BallotMeasureCard from "@/components/elections/BallotMeasureCard";
import BallotBasisNotice from "@/components/elections/BallotBasisNotice";
import DistrictFinder from "@/components/elections/DistrictFinder";
import DistrictMap from "@/components/elections/DistrictMap";
import TownContestCard from "@/components/elections/TownContestCard";
import ContestBox from "@/components/elections/ballot/ContestBox";
import BallotRaceRows from "@/components/elections/ballot/BallotRaceRows";
import ContestDrawer from "@/components/elections/ballot/ContestDrawer";
import RaceResearch from "@/components/elections/ballot/RaceResearch";
import { buildBallotContests, contestForHash, type BallotContest } from "@/lib/ballotContests";
import {
  candidateName,
  districtAreaLabel,
  formatPvi,
  isActiveCandidate,
  majorPartyOf,
  matchesDistrictQuery,
  pviColor,
  termPhrase,
  tierCandidates,
} from "@/lib/elections";
import { safeHref } from "@/lib/formatting";
import { fetchTownBallot, fetchTownsForState } from "@/lib/api";
import type {
  RaceWithCandidates,
  StateBallot,
  StateLegChamber,
  StateLegDistrict,
  StatewideNominee,
  TownBallot,
  TownEntry,
} from "@/types/election";

// ── Detail content: what the drawer shows for each kind of contest ─────

/** A nominee for an office with no FEC filing behind it: the party in
 * text, then the name in that party's colour.
 *
 * The party code is rendered, not just implied by the colour, because
 * colour alone is not an accessible way to carry information (WCAG
 * 1.4.1) — and unlike a federal row, which puts a Democrat and a
 * Republican either side of a literal "vs", these rows can hold a single
 * unopposed nominee, where there is no contrast to read the party from
 * at all. */
function NomineeName({ nominee }: { nominee: StatewideNominee }) {
  const major = majorPartyOf(nominee.party);
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="font-mono text-[10px] text-ink-min">{nominee.party}</span>
      <span
        className={
          major === "DEM" ? "text-dem-blue" : major === "REP" ? "text-rep-red" : "text-ink"
        }
      >
        {nominee.name}
      </span>
    </span>
  );
}

/** One legislative seat: its identifier, the places it covers, and who
 * is on the ballot for it.
 *
 * The town list is TRUNCATED for display while matchesDistrictQuery
 * searches the full one. A rural Minnesota senate district covers 292
 * townships, so rendering them all would bury the row — but a reader in
 * the 290th still has to find their seat by typing its name.
 */
function StateLegSeatRow({ seat }: { seat: StateLegDistrict }) {
  // Suffixes kept: a county here is the fallback for a district with
  // no incorporated place, and "Forsyth County" must not render as
  // "Forsyth" beside Georgia's actual Forsyth city.
  const townsLabel = districtAreaLabel(seat.towns, 3, false);
  return (
    <div className="grid grid-cols-[42px_1fr] items-baseline gap-3 border border-white/[0.09] bg-surface px-3 py-2">
      <span className="border border-white/15 py-0.5 text-center font-mono text-xs text-ink-hi">
        {seat.district}
      </span>
      <span className="min-w-0">
        {townsLabel && (
          <span className="block truncate text-[11px] text-ink-min">{townsLabel}</span>
        )}
        <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
          {seat.nominees.map((n) => (
            <NomineeName key={`${n.party}-${n.name}`} nominee={n} />
          ))}
        </span>
      </span>
    </div>
  );
}

/** One chamber of the state legislature: every contested seat, filtered
 * by the same grammar the U.S. House picker uses.
 *
 * These are the smallest districts on the page and there are a lot of
 * them — Rhode Island alone elects 75 representatives and 38 senators —
 * so the filter is not a convenience here, it is the only way the
 * section is usable. It matches on the towns a district covers, on a
 * candidate's name, or on the district number, because those are the
 * three things a person knows about themselves without being asked
 * where they live. Civitas does not ask.
 */
function StateLegChamberSection({ chamber }: { chamber: StateLegChamber }) {
  const [filter, setFilter] = useState("");
  const shown = chamber.districts.filter((d) =>
    matchesDistrictQuery(
      { district: d.district, areas: d.towns, candidates: d.nominees },
      filter
    )
  );

  return (
    <div className="mb-5 last:mb-0">
      <h3 className="font-mono text-xs text-ink-lo mb-1">
        {chamber.label.toUpperCase()} — {chamber.districts.length}{" "}
        {chamber.districts.length === 1 ? "SEAT" : "SEATS"} CONTESTED
        {termPhrase(chamber.termYears) && (
          <span className="text-ink-min"> · {termPhrase(chamber.termYears)!.toUpperCase()}</span>
        )}
      </h3>
      {chamber.districts.length > 3 && (
        <div className="mb-2">
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by town, candidate, or district number"
            aria-label={`Filter ${chamber.label} seats by town, candidate, or district number`}
            className="w-full min-w-0 border border-white/15 bg-surface-base px-3 py-2 font-mono text-xs text-ink-hi placeholder:text-ink-min"
          />
          <p role="status" aria-live="polite" className="sr-only">
            {filter.trim()
              ? `${shown.length} of ${chamber.districts.length} ${chamber.label} seats match ${filter}`
              : ""}
          </p>
        </div>
      )}
      <div className="space-y-1">
        {shown.map((d) => (
          <StateLegSeatRow key={d.district} seat={d} />
        ))}
        {shown.length === 0 && (
          <p className="border border-white/[0.09] p-4 text-xs text-ink-min">
            No {chamber.label} seat matches “{filter}”. Try your town, a candidate&apos;s name,
            or a district number.
          </p>
        )}
      </div>
    </div>
  );
}

function StateLegislatureDetail({ ballot }: { ballot: StateBallot }) {
  return (
    <div>
      <p className="text-xs text-ink-min mb-3">
        You vote in exactly one seat per chamber. Each is listed with the towns it covers —
        filter by yours to find it.
      </p>
      {ballot.stateLegRaces.map((chamber) => (
        <StateLegChamberSection key={chamber.chamber} chamber={chamber} />
      ))}
      <p className="mt-1 text-[10px] text-ink-min">
        Only seats named in the state&apos;s own results feed appear — a seat whose primary was
        uncontested is often not published at all, so this is not the full chamber. District
        boundaries from the U.S. Census Bureau; these offices have no federal
        campaign-finance filings, so no funding figures exist for them.
      </p>
    </div>
  );
}

/** Elected judgeships. Plain, like the executive offices, and for the
 * same reason — a judge has no FEC filing, so there is no money, no score
 * and nothing to click through to. A name and a party is the whole of
 * what's true. "Checked, and none are on this ballot" gets real words. */
function JudicialDetail({ ballot }: { ballot: StateBallot }) {
  const { judicialRaces, state } = ballot;
  if (judicialRaces.length === 0) {
    return (
      <div>
        <p className="text-sm text-ink">
          No judicial contests are on {state}&apos;s {ballot.electionDate} ballot.
        </p>
        <p className="mt-2 text-[10px] text-ink-min">
          Judges here are elected at the primary: a candidate who takes a majority
          wins the seat outright, so it never reaches the general election ballot.
          Retention questions are a separate ballot item and are not covered.
        </p>
      </div>
    );
  }

  return (
    <div>
      <p className="text-xs text-ink-min mb-3">
        Judges are elected here on a partisan ballot, the same as any other office.
      </p>
      {judicialRaces.map((court) => (
        <div key={court.court} className="mb-4 last:mb-0">
          <h3 className="font-mono text-xs text-ink-lo mb-2">
            {court.label.toUpperCase()}
            {termPhrase(court.termYears) && (
              <span className="text-ink-min"> · {termPhrase(court.termYears)!.toUpperCase()}</span>
            )}
          </h3>
          <div className="space-y-1.5">
            {court.seats.map((seat) => (
              <div
                key={seat.seat}
                className="grid grid-cols-1 gap-1 border border-white/[0.09] bg-surface px-3 py-2.5 sm:grid-cols-[minmax(0,180px)_1fr] sm:gap-3"
              >
                <span className="font-mono text-xs text-ink-lo sm:self-center">{seat.seat}</span>
                <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                  {seat.nominees.map((n) => (
                    <NomineeName key={`${n.party}-${n.name}`} nominee={n} />
                  ))}
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
      <p className="mt-1 text-[10px] text-ink-min">
        Only seats named in the state&apos;s own results feed appear — a seat whose primary
        was uncontested is often not published at all, so this is not the full bench.
        Retention questions are a separate ballot item and are not covered. These offices
        have no federal campaign-finance filings, so no funding figures exist for them.
      </p>
    </div>
  );
}

/** The state's own executive officers — Governor, Lieutenant Governor,
 * Attorney General, Secretary of State, Treasurer — where its feed
 * publishes them. "This state elects none this cycle" gets real words.
 *
 * Deliberately much plainer than a federal race: these offices have no
 * FEC filing, so there is no money, no score and nothing to click
 * through to. Showing a name and a party is the whole of what's true.
 */
function StatewideExecutiveDetail({ ballot }: { ballot: StateBallot }) {
  const { statewideRaces, statewideCoverage, state } = ballot;
  return (
    <div>
      {statewideRaces.length === 0 ? (
        <p className="text-sm text-ink">
          No statewide executive offices are on {state}&apos;s {ballot.electionDate} ballot.
        </p>
      ) : (
        <div className="space-y-1.5">
          {statewideRaces.map((race) => (
            <div
              key={race.office}
              className="grid grid-cols-1 gap-1 border border-white/[0.09] bg-surface px-3 py-2.5 sm:grid-cols-[minmax(0,180px)_1fr] sm:gap-3"
            >
              <span className="font-mono text-xs text-ink-lo sm:self-center">
                {race.label}
                {termPhrase(race.termYears) && (
                  <span className="block text-ink-min">{termPhrase(race.termYears)}</span>
                )}
              </span>
              <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                {race.nominees.map((n) => (
                  <NomineeName key={`${n.party}-${n.name}`} nominee={n} />
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 text-[10px] text-ink-min">
        {statewideCoverage.sourceName
          ? `Nominees as published by ${statewideCoverage.sourceName}`
          : "Nominees as published by the state"}
        {statewideCoverage.checkedAt
          ? ` · last checked ${statewideCoverage.checkedAt.slice(0, 10)}`
          : ""}
        .
        {/* Only meaningful next to actual nominees: an office whose primary
            nobody contested is often not itemised in a results feed at all,
            so this list is what the state published, not necessarily every
            statewide office on the ballot. */}
        {statewideRaces.length > 0 &&
          " Only offices named in the state's own results feed appear — one whose primary was uncontested is often not published at all. These offices have no federal campaign-finance filings, so no funding figures or Representation Scores exist for them."}
      </p>
    </div>
  );
}

/** No state office of any kind on file for this state. Says what is
 * missing and where to look, and never implies the state elects no one —
 * the same claim-discipline as MeasuresSection's "not loaded" case. */
function StateOfficesNotLoaded({ ballot, lookupHref }: { ballot: StateBallot; lookupHref: string }) {
  return (
    <div className="border border-signal-amber/40 bg-signal-amber/10 p-4">
      <p className="text-sm text-signal-amber">
        Civitas does not have {ballot.state}&apos;s own offices yet — its statewide offices,
        state legislature and elected judges.
      </p>
      <p className="text-xs text-ink-lo mt-2">
        This does <strong>not</strong> mean there are none on the ballot: those contests are read
        state by state from each state&apos;s own results, and this one is not covered yet. Use the{" "}
        <a href={lookupHref} target="_blank" rel="noopener noreferrer" className="text-signal-cyan hover:text-phos">
          official lookup ↗
        </a>{" "}
        to see every contest on your ballot.
      </p>
    </div>
  );
}

/** The measures, including the three ways the list can be empty.
 *
 * "This state has no measures" and "we don't know this state's measures"
 * are different claims. An empty list under a heading like "Statewide
 * ballot measures" reads as the first, so a state we simply have not
 * ingested — 17 amendments and all — would silently tell a voter there is
 * nothing to research.
 */
function MeasuresSection({ ballot, lookupHref }: { ballot: StateBallot; lookupHref: string }) {
  const { measures, measureCoverage, state } = ballot;

  if (measures.length > 0) {
    return (
      <div className="space-y-3">
        {measures.map((m) => (
          <BallotMeasureCard key={m.id} measure={m} />
        ))}
        <p className="text-[10px] text-ink-min">
          {measures.length} statewide {measures.length === 1 ? "measure" : "measures"} on
          record from {measureCoverage.sourceName || "the source"}
          {measureCoverage.checkedAt
            ? ` · last checked ${measureCoverage.checkedAt.slice(0, 10)}`
            : ""}
          . Local measures on your ballot are not shown here.
        </p>
      </div>
    );
  }

  if (measureCoverage.status === "confirmed_none") {
    return (
      <div className="border border-white/15 p-4">
        <p className="text-sm text-ink">
          No statewide ballot measures are on {state}&apos;s {ballot.electionDate} ballot.
        </p>
        <p className="text-[10px] text-ink-min mt-2">
          Per {measureCoverage.sourceName || "our source"}
          {measureCoverage.checkedAt
            ? `, checked ${measureCoverage.checkedAt.slice(0, 10)}`
            : ""}
          . Your county or city may still have local measures — check the{" "}
          <a href={lookupHref} target="_blank" rel="noopener noreferrer" className="text-signal-cyan hover:text-phos">
            official lookup ↗
          </a>
          .
        </p>
      </div>
    );
  }

  // not_yet_covered / ingest_failed — say so plainly. Never imply zero.
  return (
    <div className="border border-signal-amber/40 bg-signal-amber/10 p-4">
      <p className="text-sm text-signal-amber">
        Civitas does not have {state}&apos;s statewide ballot measures yet.
      </p>
      <p className="text-xs text-ink-lo mt-2">
        This does <strong>not</strong> mean there are none —{" "}
        {measureCoverage.status === "ingest_failed"
          ? "our last attempt to load them failed"
          : "we have not ingested this state yet"}
        . Use the{" "}
        <a href={lookupHref} target="_blank" rel="noopener noreferrer" className="text-signal-cyan hover:text-phos">
          official lookup ↗
        </a>{" "}
        to see everything on your ballot.
      </p>
      {measureCoverage.checkedAt && (
        <p className="text-[10px] text-ink-min mt-2">
          Last attempt {measureCoverage.checkedAt.slice(0, 10)}.
        </p>
      )}
    </div>
  );
}

/** Local (town-level) races and measures — additive to the statewide
 * content, never a replacement. Only offered when the backend has curated
 * towns for this state (backend/app/data/town_directory.json).
 *
 * Resolved against a fixed, public representative address (e.g. town
 * hall) chosen by Civitas, never one a visitor types in — see
 * GOOGLE_CIVIC_API_KEY's comment in config.py for why. That is a real
 * approximation, not a precinct-accurate lookup, and the copy below says
 * so: a town can contain more than one precinct.
 */
function TownDetail({
  state,
  towns,
  pageElectionDate,
}: {
  state: string;
  towns: TownEntry[];
  pageElectionDate: string;
}) {
  const [selected, setSelected] = useState("");
  const [ballot, setBallot] = useState<TownBallot | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    fetchTownBallot(state, selected)
      .then((b) => {
        if (!cancelled) setBallot(b);
      })
      .catch(() => {
        if (!cancelled) {
          setBallot({
            status: "ingest_failed", address: null, source: null, sourceUrl: null,
            electionName: null, electionDate: null, contests: [],
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [state, selected]);

  return (
    <div>
      <p className="text-[11px] text-ink-min mb-3">
        Optional and approximate: results are resolved against a fixed, public address
        in the town you pick (e.g. town hall) — never an address you type in. If your
        own precinct differs from that address within town limits, some local races
        here may not match yours.
      </p>
      <select
        value={selected}
        onChange={(e) => {
          setSelected(e.target.value);
          setBallot(null);
          setLoading(Boolean(e.target.value));
        }}
        className="bg-surface-base border border-white/15 text-ink-hi font-mono text-xs px-3 py-2 mb-4 min-h-[44px]"
        aria-label="Select your town for local races (optional, approximate)"
      >
        <option value="">— choose a town —</option>
        {towns.map((t) => (
          <option key={t.name} value={t.name}>
            {t.name}
          </option>
        ))}
      </select>

      {selected && loading && (
        <p className="text-xs text-ink-min">Loading {selected}&apos;s local races…</p>
      )}

      {selected && !loading && ballot?.status === "covered" && (
        ballot.contests.length > 0 ? (
          <div className="space-y-3">
            {ballot.electionDate && ballot.electionDate !== pageElectionDate && (
              // Load-bearing, not decoration: this source's most recently
              // published ballot can be an EARLIER election (a September
              // primary) than the general election this page is titled
              // for. Showing those candidates with no warning would
              // misstate what's actually on the November ballot.
              <div className="border border-signal-amber/40 bg-signal-amber/10 p-3">
                <p className="text-xs text-signal-amber">
                  These local races are from {selected}&apos;s{" "}
                  {ballot.electionName || "most recently published ballot"}
                  {ballot.electionDate ? ` (${ballot.electionDate})` : ""} —{" "}
                  <strong>not</strong> the {pageElectionDate} general election.{" "}
                  {selected} has not yet published a ballot for that election.
                </p>
              </div>
            )}
            {ballot.contests.map((item, i) => (
              <TownContestCard key={i} item={item} />
            ))}
            {ballot.source && (
              <p className="text-[10px] text-ink-min">
                {ballot.address
                  ? `Resolved against ${ballot.address} · ${ballot.source}`
                  : (() => {
                      const href = safeHref(ballot.sourceUrl);
                      return href ? (
                        <>
                          Source:{" "}
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`${ballot.source} (opens in new tab)`}
                            className="text-signal-cyan hover:text-phos"
                          >
                            {ballot.source} ↗
                          </a>
                        </>
                      ) : (
                        `Source: ${ballot.source}`
                      );
                    })()}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-ink-lo">No local races on file for {selected} this cycle.</p>
        )
      )}

      {selected && !loading && ballot?.status === "ingest_failed" && (
        <p className="text-xs text-signal-amber">
          Could not load {selected}&apos;s local races right now — try again shortly.
        </p>
      )}
    </div>
  );
}

/** One district in the House picker: number, area, lean, and — reusing
 * the tierCandidates split — the leading D/R names, so a reader can find
 * their district by the names they know as well as by county. */
function HouseDistrictOption({
  race,
  onPick,
}: {
  race: RaceWithCandidates;
  onPick: () => void;
}) {
  const { leaders } = tierCandidates(race.candidates.filter(isActiveCandidate));
  const dem = leaders.find((c) => majorPartyOf(c.party) === "DEM");
  const rep = leaders.find((c) => majorPartyOf(c.party) === "REP");
  const countiesLabel = districtAreaLabel(race.counties);

  return (
    <button
      type="button"
      onClick={onPick}
      className="mb-1.5 grid w-full grid-cols-[34px_1fr_auto] items-center gap-3 border border-white/[0.09] bg-surface px-3 py-2.5 text-left hover:border-white/30"
    >
      <span className="border border-white/15 py-0.5 text-center font-mono text-sm text-ink-hi">
        {race.district === 0 ? "AL" : race.district}
      </span>
      <span className="min-w-0">
        {countiesLabel && <span className="block truncate text-[11px] text-ink-min">{countiesLabel}</span>}
        <span className="flex flex-wrap items-baseline gap-x-1.5 text-sm">
          {dem ? (
            <span className="text-dem-blue">
              {candidateName(dem)}
              {dem.incumbentChallenge === "I" ? " (I)" : ""}
            </span>
          ) : (
            <span className="text-ink-min">no funded Democrat</span>
          )}
          <span className="text-[11px] text-ink-min">vs</span>
          {rep ? (
            <span className="text-rep-red">
              {candidateName(rep)}
              {rep.incumbentChallenge === "I" ? " (I)" : ""}
            </span>
          ) : (
            <span className="text-ink-min">no funded Republican</span>
          )}
        </span>
      </span>
      <span className="flex flex-col items-end gap-0.5 whitespace-nowrap">
        <span className={`font-mono text-xs ${pviColor(race.pvi)}`}>
          {formatPvi(race.pvi)}
          {/* No district-level PVI crosswalk data for this district yet —
              the number shown is this whole state's lean, not this
              district's. */}
          {race.pviLevel === "state" && <span className="text-ink-min"> (statewide)</span>}
        </span>
      </span>
    </button>
  );
}

/** The U.S. Representative contest: pick your district, then research it.
 *
 * Civitas never asks a visitor for their address, so finding "your"
 * district is a navigation problem: point at the map, pick your county,
 * or filter by a county, a representative's name or a district number. */
function HouseDetail({
  ballot,
  pickedId,
  onPick,
}: {
  ballot: StateBallot;
  pickedId: string | null;
  onPick: (id: string | null) => void;
}) {
  const houseRaces = ballot.houseRaces;
  const [filter, setFilter] = useState("");
  const picked = houseRaces.find((r) => r.id === pickedId) ?? (houseRaces.length === 1 ? houseRaces[0] : null);

  if (picked) {
    const stories = ballot.coverage.filter((c) => c.race?.id === picked.id);
    return (
      <div>
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[15px] font-bold text-ink-hi">
              {picked.district === 0 ? "At-large seat" : `District ${picked.district}`}
              <span className={`ml-2 font-mono text-xs font-normal ${pviColor(picked.pvi)}`}>
                {formatPvi(picked.pvi)}
                {picked.pviLevel === "state" && <span className="text-ink-min"> (statewide)</span>}
              </span>
            </p>
            {picked.counties && picked.counties.length > 0 && (
              <p className="mt-0.5 font-mono text-xs text-ink-min">Covers: {picked.counties.join(", ")}</p>
            )}
          </div>
          {houseRaces.length > 1 && (
            <button
              type="button"
              onClick={() => onPick(null)}
              className="min-h-[44px] font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
            >
              ← PICK ANOTHER DISTRICT
            </button>
          )}
        </div>
        <RaceResearch
          race={picked}
          coverage={stories}
          supersededByPrimary={ballot.ballotBasis?.supersededByPrimary ?? false}
        />
      </div>
    );
  }

  const shown = houseRaces.filter((r) => matchesDistrictQuery({ ...r, areas: r.counties }, filter));
  return (
    <div>
      <p className="mb-3 text-[13px] text-ink-lo">
        You vote in exactly one of these. Point at the map, pick your county, or filter by a
        county, a representative&apos;s name or a district number — or{" "}
        <a
          href="https://www.house.gov/representatives/find-your-representative"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Find your representative at house.gov (opens in new tab)"
          className="text-signal-cyan hover:text-phos"
        >
          look it up at house.gov ↗
        </a>
        .
      </p>
      <DistrictMap state={ballot.state} races={houseRaces} picked={null} onPick={(id) => onPick(id)} />
      {houseRaces.length > 3 && <DistrictFinder races={houseRaces} picked={null} onPick={onPick} />}
      {houseRaces.length > 3 && (
        <div className="mb-3">
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by county, representative, or district number"
            aria-label="Filter districts by county, representative, or district number"
            className="w-full min-w-0 border border-white/15 bg-surface-base px-3 py-2 font-mono text-xs text-ink-hi placeholder:text-ink-min"
          />
          <p className="mt-1.5 text-[10px] text-ink-min">
            Filtered here in your browser — nothing is sent anywhere, and Civitas never asks for
            your address.
          </p>
          {/* Typing silently rewrites the list below, which a sighted
              reader sees and a screen-reader user otherwise would not. */}
          <p role="status" aria-live="polite" className="sr-only">
            {filter.trim() ? `${shown.length} of ${houseRaces.length} districts match ${filter}` : ""}
          </p>
        </div>
      )}
      {shown.map((r) => (
        <HouseDistrictOption key={r.id} race={r} onPick={() => onPick(r.id)} />
      ))}
      {shown.length === 0 && (
        <p className="border border-white/[0.09] p-4 text-xs text-ink-min">
          No district matches “{filter}”. Try a county name, your representative&apos;s
          surname, or a district number — or pick a county above.
        </p>
      )}
    </div>
  );
}

// ── The ballot itself: boxes in three columns ─────────────────────────

function OpenButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="min-h-[44px] w-full px-4 text-left font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
    >
      {label} →
    </button>
  );
}

function ContestOverview({
  contest,
  ballot,
  onOpen,
}: {
  contest: BallotContest;
  ballot: StateBallot;
  onOpen: (key: string, houseRaceId?: string | null) => void;
}) {
  const box = (children: ReactNode) => (
    <ContestBox title={contest.title} subtitle={contest.subtitle} instruction={contest.instruction}>
      {children}
    </ContestBox>
  );

  switch (contest.kind) {
    case "senate": {
      const race = contest.race!;
      const stories = ballot.coverage.filter((c) => c.race?.id === race.id).length;
      return box(
        <>
          <BallotRaceRows race={race} />
          <OpenButton
            label={`RESEARCH THIS RACE${stories ? ` · ${stories} ${stories === 1 ? "STORY" : "STORIES"}` : ""}`}
            onClick={() => onOpen(contest.key)}
          />
        </>,
      );
    }
    case "house": {
      if (ballot.houseRaces.length === 1) {
        return box(
          <>
            <BallotRaceRows race={ballot.houseRaces[0]} />
            <OpenButton label="RESEARCH THIS RACE" onClick={() => onOpen("house", ballot.houseRaces[0].id)} />
          </>,
        );
      }
      return box(
        <div className="px-4 py-3">
          <p className="mb-2 text-[13px] text-ink-lo">Pick a district to research its race:</p>
          <div className="grid grid-cols-7 gap-1.5">
            {ballot.houseRaces.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => onOpen("house", r.id)}
                aria-label={r.district === 0 ? "At-large district" : `District ${r.district}`}
                className="min-h-[44px] border border-white/25 font-mono text-sm text-ink-hi hover:border-phos hover:text-phos"
              >
                {r.district === 0 ? "AL" : r.district}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => onOpen("house", null)}
            className="mt-2 min-h-[44px] font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
          >
            DON&apos;T KNOW YOUR DISTRICT? MAP OR COUNTY →
          </button>
        </div>,
      );
    }
    case "statewide":
      return box(
        ballot.statewideRaces.length === 0 ? (
          <p className="px-4 py-3 text-[13px] text-ink-lo">
            No statewide executive offices are on this ballot.
          </p>
        ) : (
          <>
            <ul>
              {ballot.statewideRaces.map((r) => (
                <li key={r.office} className="border-b border-white/[0.09] px-4 py-2">
                  <span className="block text-[13px] font-bold text-ink-hi">
                    {r.label}
                    {termPhrase(r.termYears) && (
                      <span className="font-normal text-ink-lo"> · {termPhrase(r.termYears)}</span>
                    )}
                  </span>
                  <span className="flex flex-wrap gap-x-3 text-sm">
                    {r.nominees.map((n) => (
                      <NomineeName key={`${n.party}-${n.name}`} nominee={n} />
                    ))}
                  </span>
                </li>
              ))}
            </ul>
            <OpenButton label="SOURCE AND NOTES" onClick={() => onOpen(contest.key)} />
          </>
        ),
      );
    case "stateleg":
      return box(
        <>
          <ul>
            {ballot.stateLegRaces.map((c) => (
              <li key={c.chamber} className="border-b border-white/[0.09] px-4 py-2 text-[13px] text-ink-lo">
                <span className="font-bold text-ink-hi">{c.label}</span> · {c.districts.length}{" "}
                {c.districts.length === 1 ? "seat" : "seats"} contested
                {termPhrase(c.termYears) && ` · ${termPhrase(c.termYears)}`}
              </li>
            ))}
          </ul>
          <OpenButton label="FIND YOUR SEATS BY TOWN" onClick={() => onOpen(contest.key)} />
        </>,
      );
    case "judicial":
      return box(
        ballot.judicialRaces.length === 0 ? (
          <p className="px-4 py-3 text-[13px] text-ink-lo">
            None on this ballot — judges here are elected at the primary.
          </p>
        ) : (
          <>
            <ul>
              {ballot.judicialRaces.map((c) => (
                <li key={c.court} className="border-b border-white/[0.09] px-4 py-2 text-[13px] text-ink-lo">
                  <span className="font-bold text-ink-hi">{c.label}</span> · {c.seats.length}{" "}
                  {c.seats.length === 1 ? "seat" : "seats"}
                  {termPhrase(c.termYears) && ` · ${termPhrase(c.termYears)}`}
                </li>
              ))}
            </ul>
            <OpenButton label="SEE THE CANDIDATES" onClick={() => onOpen(contest.key)} />
          </>
        ),
      );
    case "stateNone":
      return box(
        <>
          <p className="px-4 pt-3 text-[13px] text-ink-lo">
            Civitas does not have this state&apos;s own offices yet — that does not mean there are
            none on the ballot.
          </p>
          <OpenButton label="DETAILS" onClick={() => onOpen(contest.key)} />
        </>,
      );
    case "measures":
      return box(
        ballot.measures.length > 0 ? (
          <>
            <ul>
              {ballot.measures.map((m) => (
                <li key={m.id} className="border-b border-white/[0.09] px-4 py-2 text-[13px] text-ink-hi">
                  <span className="mr-2 font-mono text-xs text-ink-lo">{m.number}</span>
                  {m.title}
                </li>
              ))}
            </ul>
            <OpenButton label="READ THE MEASURES" onClick={() => onOpen(contest.key)} />
          </>
        ) : (
          <>
            <p className="px-4 pt-3 text-[13px] text-ink-lo">
              {ballot.measureCoverage.status === "confirmed_none"
                ? "No statewide measures are on record for this ballot."
                : "Civitas does not have this state's measures yet — that does not mean there are none."}
            </p>
            <OpenButton label="DETAILS" onClick={() => onOpen(contest.key)} />
          </>
        ),
      );
    case "local":
      return box(<OpenButton label="CHOOSE YOUR TOWN" onClick={() => onOpen(contest.key)} />);
    case "news":
      return box(
        <OpenButton
          label={ballot.coverage.length ? `READ ${ballot.coverage.length} STORIES` : "NO COVERAGE YET"}
          onClick={() => onOpen(contest.key)}
        />,
      );
  }
}

/** Why a column that should hold the Senate race doesn't: the three-class
 * rotation means most states have no regular Senate race most cycles, and
 * an absent box reads as missing data rather than "not up until later". */
function NoSenateBox({ ballot }: { ballot: StateBallot }) {
  return (
    <ContestBox title="U.S. Senator" subtitle="Not on this ballot">
      <p className="px-4 py-3 text-[13px] text-ink-lo">
        Neither of {ballot.state}&apos;s Senate seats is up for election in {ballot.cycleYear}.
        Senators serve staggered six-year terms, so a state votes on one of its two seats roughly
        once every three cycles. This state&apos;s next regular Senate election is in{" "}
        {ballot.nextSenateElection}.
      </p>
    </ContestBox>
  );
}

const COLUMN_TITLES: Record<BallotContest["column"], string> = {
  federal: "FEDERAL OFFICES",
  state: "STATE OFFICES",
  local: "MEASURES · LOCAL",
};

export default function StateBallotClient({ ballot }: { ballot: StateBallot }) {
  const { officialLookup } = ballot;
  // officialLookup.url comes from state_ballot_lookup.json via the API,
  // same external-data trust boundary CoverageFeed.tsx guards for article
  // URLs. This is "the one link on the page whose failure strands the
  // visitor" (election_pipeline.py), so on a malformed/unsafe URL this
  // falls back to the same USAGov default the backend itself falls back to.
  const lookupHref = safeHref(officialLookup.url) || "https://www.usa.gov/election-office";
  const stateName = ballot.stateName ?? ballot.state;

  const [towns, setTowns] = useState<TownEntry[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchTownsForState(ballot.state)
      .then((t) => {
        if (!cancelled) setTowns(t);
      })
      .catch(() => {
        if (!cancelled) setTowns([]);
      });
    return () => {
      cancelled = true;
    };
  }, [ballot.state]);

  const contests = useMemo(() => buildBallotContests(ballot, towns.length > 0), [ballot, towns.length]);
  // undefined = no choice made yet, so defer to the URL; null = closed.
  // Collapsing the two meant a contest opened by a link could never be
  // closed (setting null over null changes nothing).
  const [chosen, setChosen] = useState<{ key: string; houseRaceId: string | null } | null | undefined>(
    undefined,
  );
  // A #race-{id} link (old /elections/{raceId} redirects, Bluesky posts)
  // or a #ballot-{key} link opens that contest. The server render has no
  // hash, so it is read only once mounted — the useMounted idiom avoids a
  // hydration mismatch without setting state in an effect.
  const mounted = useMounted();
  const fromHash = mounted ? contestForHash(window.location.hash, contests, ballot) : null;
  const open = chosen !== undefined ? chosen : fromHash;

  const openContest = useCallback(
    (key: string, houseRaceId: string | null = null) => {
      setChosen({ key, houseRaceId });
      const race = contests.find((c) => c.key === key)?.race;
      const hash = houseRaceId ? `#race-${houseRaceId}` : race ? `#race-${race.id}` : `#ballot-${key}`;
      window.history.replaceState(null, "", hash);
    },
    [contests],
  );
  const closeContest = useCallback(() => {
    setChosen(null);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }, []);

  const openIndex = open ? contests.findIndex((c) => c.key === open.key) : -1;
  const openContestEntry = openIndex >= 0 ? contests[openIndex] : null;

  // What the header counts — the ballot's shape at a glance.
  const federalRaces = [...ballot.senateRaces, ...ballot.houseRaces];
  const federalCandidates = federalRaces.flatMap((r) => r.candidates.filter(isActiveCandidate));
  const thirdParty = federalCandidates.filter((c) => majorPartyOf(c.party) === null).length;
  const withRecords = federalCandidates.filter((c) => c.incumbentRecord).length;

  function detailFor(contest: BallotContest) {
    switch (contest.kind) {
      case "senate":
        return (
          <RaceResearch
            race={contest.race!}
            coverage={ballot.coverage.filter((c) => c.race?.id === contest.race!.id)}
            supersededByPrimary={ballot.ballotBasis?.supersededByPrimary ?? false}
          />
        );
      case "house":
        return (
          <HouseDetail
            ballot={ballot}
            pickedId={open?.houseRaceId ?? null}
            onPick={(id) => openContest("house", id)}
          />
        );
      case "statewide":
        return <StatewideExecutiveDetail ballot={ballot} />;
      case "stateleg":
        return <StateLegislatureDetail ballot={ballot} />;
      case "judicial":
        return <JudicialDetail ballot={ballot} />;
      case "stateNone":
        return <StateOfficesNotLoaded ballot={ballot} lookupHref={lookupHref} />;
      case "measures":
        return <MeasuresSection ballot={ballot} lookupHref={lookupHref} />;
      case "local":
        return <TownDetail state={ballot.state} towns={towns} pageElectionDate={ballot.electionDate} />;
      case "news":
        return <CoverageFeed items={ballot.coverage} />;
    }
  }

  const columns: BallotContest["column"][] = ["federal", "state", "local"];

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="mx-auto max-w-[1400px]">
          <Link
            href="/elections"
            className="inline-block mb-4 font-mono text-xs text-ink-lo hover:text-phos transition-colors"
          >
            ← ALL STATES
          </Link>

          <header className="mb-5 flex flex-col gap-4 border-b border-white/[0.14] pb-5 font-sans lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <p className="font-mono text-xs tracking-[0.14em] text-phos">
                BALLOT RESEARCH · {stateName.toUpperCase()} · {ballot.electionDate.toUpperCase()}
              </p>
              <h1 className="mt-1 font-display text-2xl font-extrabold text-ink-hi sm:text-[28px]">
                Everyone on {stateName}&apos;s ballot, and who is behind them
              </h1>
              <p className="mt-1.5 text-sm text-ink-lo">
                {contests.length} contests · {federalCandidates.length} federal candidates
                {thirdParty > 0 && `, ${thirdParty} outside the two major parties`}
                {withRecords > 0 && ` · ${withRecords} with a congressional voting record`}
                {ballot.statePvi !== null && (
                  <>
                    {" · "}
                    <span className={`font-mono ${pviColor(ballot.statePvi)}`}>{formatPvi(ballot.statePvi)}</span>{" "}
                    statewide lean
                  </>
                )}
              </p>
              {ballot.primaryDate && (
                <p className="mt-0.5 font-mono text-xs text-ink-min">PRIMARY: {ballot.primaryDate}</p>
              )}
              {ballot.statePvi !== null && (
                <div className="mt-1 max-w-2xl">
                  <PviMethodologyNote />
                </div>
              )}
            </div>
            <div className="flex shrink-0 flex-col gap-1 lg:items-end lg:text-right">
              <span className="text-xs text-ink-lo">A research tool, not an official ballot.</span>
              <a
                href={lookupHref}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`${officialLookup.label} (opens in new tab)`}
                className="font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
              >
                {officialLookup.isStateSpecific
                  ? `SEE YOUR FULL ${ballot.state} BALLOT ↗`
                  : "FIND YOUR ELECTION OFFICE ↗"}
              </a>
            </div>
          </header>

          {/* What the candidate lists on this page actually ARE — above the
              ballot, not under it: in a state still on FEC filers after its
              primary, a reader who only sees a footnote has been misled. */}
          <BallotBasisNotice basis={ballot.ballotBasis} />

          {federalRaces.length === 0 && (
            <p className="mb-4 text-base text-ink-min">
              No federal races on record for {ballot.state} in {ballot.cycleYear} yet.
            </p>
          )}

          {/* Desktop: the ballot as it is printed — three columns, each
              contest a box, fitting about one screen. The research for any
              contest opens beside it. */}
          <div className="hidden gap-5 lg:grid lg:grid-cols-3" data-testid="ballot-columns">
            {columns.map((col) => (
              <div key={col} className="flex flex-col gap-4">
                <h2 className="font-mono text-xs tracking-[0.16em] text-phos">{COLUMN_TITLES[col]}</h2>
                {col === "federal" &&
                  ballot.senateRaces.length === 0 &&
                  ballot.nextSenateElection !== null &&
                  ballot.houseRaces.length > 0 && <NoSenateBox ballot={ballot} />}
                {contests
                  .filter((c) => c.column === col)
                  .map((c) => (
                    <ContestOverview key={c.key} contest={c} ballot={ballot} onOpen={openContest} />
                  ))}
                {col === "local" && (
                  <ContestBox title="Not on this page">
                    <p className="px-4 pt-3 text-[13px] text-ink-lo">
                      Ballots are printed per precinct, so some of what you will vote on cannot be
                      shown on a statewide page:
                    </p>
                    <ul className="list-disc px-4 pb-3 pl-8 pt-1 text-[13px] text-ink-lo">
                      {ballot.omits.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </ContestBox>
                )}
              </div>
            ))}
          </div>

          {/* Phone: the index of every contest, one line each; a contest
              opens on its own screen with Previous/Next. */}
          <div className="lg:hidden" data-testid="ballot-index">
            <nav aria-label="Contests on this ballot" className="border border-white/25 bg-surface font-sans">
              {columns.map((col) => {
                const inCol = contests.filter((c) => c.column === col);
                if (inCol.length === 0) return null;
                return (
                  <div key={col}>
                    <p className="px-4 pb-1.5 pt-3 font-mono text-[11px] tracking-[0.16em] text-phos">
                      {COLUMN_TITLES[col]}
                    </p>
                    <ul>
                      {inCol.map((c) => (
                        <li key={c.key}>
                          <button
                            type="button"
                            onClick={() => openContest(c.key)}
                            className="flex min-h-[52px] w-full items-center gap-3 border-b border-white/[0.14] px-4 py-2.5 text-left"
                          >
                            <span className="w-5 font-mono text-xs text-ink-min">{contests.indexOf(c) + 1}</span>
                            <span className="flex min-w-0 flex-1 flex-col">
                              <span className="text-[15px] font-bold text-ink-hi">{c.title}</span>
                              <span className="text-[13px] text-ink-lo">{c.summary}</span>
                            </span>
                            <span aria-hidden="true" className="text-ink-lo">›</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </nav>
            {contests.length > 0 && (
              <button
                type="button"
                onClick={() => openContest(contests[0].key)}
                className="mt-3 min-h-[48px] w-full bg-phos font-bold text-surface-base hover:bg-phos-mid"
              >
                Research contest 1 →
              </button>
            )}
            <details className="mt-3 border border-white/25 bg-surface p-4">
              <summary className="cursor-pointer text-sm font-bold text-ink-hi">Not on this page</summary>
              <ul className="mt-2 list-disc pl-5 text-[13px] text-ink-lo">
                {ballot.omits.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </details>
          </div>
        </div>
      </main>

      {openContestEntry && (
        <ContestDrawer
          contest={openContestEntry}
          index={openIndex}
          total={contests.length}
          prev={openIndex > 0 ? contests[openIndex - 1] : null}
          next={openIndex < contests.length - 1 ? contests[openIndex + 1] : null}
          onNavigate={(key) => openContest(key)}
          onClose={closeContest}
        >
          {detailFor(openContestEntry)}
        </ContestDrawer>
      )}

      <BackToTop />
      <Footer />
    </div>
  );
}
