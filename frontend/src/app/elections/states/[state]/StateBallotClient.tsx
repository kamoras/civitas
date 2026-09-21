"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import RaceFullDetail from "@/components/elections/RaceFullDetail";
import CoverageFeed, { useMounted } from "@/components/elections/CoverageFeed";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import BallotMeasureCard from "@/components/elections/BallotMeasureCard";
import TownContestCard from "@/components/elections/TownContestCard";
import { districtAreaLabel, formatPvi, majorPartyOf, matchesDistrictQuery, pviColor, tierCandidates } from "@/lib/elections";
import { safeHref } from "@/lib/formatting";
import { fetchTownBallot, fetchTownsForState } from "@/lib/api";
import type {
  StateBallot,
  StateLegChamber,
  StateLegDistrict,
  StatewideNominee,
  TownBallot,
  TownEntry,
} from "@/types/election";

/** One district's collapsed row: number, area, lean, and — reusing the
 * exact same tierCandidates split the Senate section renders leader
 * cards from — the leading D/R names, so a reader learns this page's one
 * visual grammar once and reads it correctly here too. Expands in place
 * on click to the same RaceFullDetail every other race uses; there is no
 * separate "district detail" view. */
function HouseDistrictRow({
  race,
  open,
  onToggle,
}: {
  race: StateBallot["houseRaces"][number];
  open: boolean;
  onToggle: () => void;
}) {
  const { leaders } = tierCandidates(race.candidates);
  const dem = leaders.find((c) => majorPartyOf(c.party) === "DEM");
  const rep = leaders.find((c) => majorPartyOf(c.party) === "REP");
  const countiesLabel = districtAreaLabel(race.counties);
  const filedCount = race.candidates.length;

  return (
    <div id={`race-${race.id}`} className="scroll-mt-24 border border-white/[0.09] bg-surface mb-1.5">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="grid w-full grid-cols-[34px_1fr_auto] items-center gap-3 px-3 py-2.5 text-left hover:bg-white/[0.02]"
      >
        <span className="border border-white/15 py-0.5 text-center font-mono text-sm text-ink-hi">
          {race.district === 0 ? "AL" : race.district}
        </span>
        <span className="min-w-0">
          {countiesLabel && (
            <span className="block truncate text-[11px] text-ink-min">{countiesLabel}</span>
          )}
          <span className="flex flex-wrap items-baseline gap-x-1.5 text-sm">
            {dem ? (
              <span className="text-dem-blue">
                {dem.name}
                {dem.incumbentChallenge === "I" ? " (I)" : ""}
              </span>
            ) : (
              <span className="text-ink-min">no funded Democrat</span>
            )}
            <span className="text-[11px] text-ink-min">vs</span>
            {rep ? (
              <span className="text-rep-red">
                {rep.name}
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
                district's. Silently showing the same figure with no
                qualifier previously let it read as a precise district
                measure. */}
            {race.pviLevel === "state" && <span className="text-ink-min"> (statewide)</span>}
          </span>
          <span className="text-[10px] text-ink-min">{filedCount} filed</span>
        </span>
      </button>
      {open && (
        <div className="border-t border-white/[0.09] p-4">
          {race.counties && race.counties.length > 0 && (
            <p className="mb-3 font-mono text-xs text-ink-min">Covers: {race.counties.join(", ")}</p>
          )}
          <RaceFullDetail race={race} />
        </div>
      )}
    </div>
  );
}

function HouseSection({ houseRaces }: { houseRaces: StateBallot["houseRaces"] }) {
  // Civitas never asks a visitor for their address. Finding "your"
  // district is therefore a navigation problem, solved with the signals
  // each row already carries — its counties and its sitting
  // representative — rather than by collecting a street address and
  // geocoding it (an address box lived here until 2026-09; it was
  // resolve-only and never stored, but collecting the address at all was
  // the wrong shape for this project).
  const [filter, setFilter] = useState("");
  const shown = houseRaces.filter(
    (r) => matchesDistrictQuery({ ...r, areas: r.counties }, filter)
  );

  // undefined = "no explicit choice yet" (defer to the hash), distinct
  // from null = "explicitly closed" — collapsing those into one `null`
  // meant clicking a hash-opened row to close it called setOpenId(null)
  // on a value that was ALREADY null, so nothing changed and the row
  // could never be closed by the user.
  const [openId, setOpenId] = useState<string | null | undefined>(undefined);

  // A #race-{id} deep link (old /elections/{raceId} redirects, and
  // Bluesky post links) opens that district's row. SSR always renders
  // nothing open (window is undefined there), so the hash is only read
  // once mounted — same useSyncExternalStore idiom CoverageFeed uses to
  // avoid a hydration mismatch, here avoiding a setState-in-effect too.
  const mounted = useMounted();
  const hashRaceId = mounted ? (window.location.hash.match(/^#race-(.+)$/)?.[1] ?? null) : null;
  const openRaceId = openId !== undefined ? openId : hashRaceId;

  useEffect(() => {
    if (openRaceId) {
      document.getElementById(`race-${openRaceId}`)?.scrollIntoView();
    }
  }, [openRaceId]);

  return (
    <section className="panel mb-6">
      <TerminalTitlebar title="House" />
      <div className="p-6">
        <h2 className="font-mono text-xs text-ink-lo mb-1">
          U.S. HOUSE — {houseRaces.length} {houseRaces.length === 1 ? "DISTRICT" : "DISTRICTS"}
        </h2>
        <p className="text-xs text-ink-min mb-3">
          You vote in exactly one of these — every district is listed below, each with the counties
          it covers and its current representative. Filter by any of those to find yours, or{" "}
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

        {/* Only worth the row of chrome once the list is long enough to
            be a scroll; a 1-2 district state is already fully visible. */}
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
                reader sees and a screen-reader user otherwise would not.
                Announced only once a filter is active, so simply landing
                on the page doesn't read out a district count. */}
            <p role="status" aria-live="polite" className="sr-only">
              {filter.trim()
                ? `${shown.length} of ${houseRaces.length} districts match ${filter}`
                : ""}
            </p>
          </div>
        )}

        <div className="mt-1">
          {shown.map((r) => (
            <HouseDistrictRow
              key={r.id}
              race={r}
              open={r.id === openRaceId}
              onToggle={() => setOpenId(r.id === openRaceId ? null : r.id)}
            />
          ))}
          {shown.length === 0 && (
            <p className="border border-white/[0.09] p-4 text-xs text-ink-min">
              No district matches “{filter}”. Try a county name, your representative&apos;s
              surname, or a district number.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

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
  const townsLabel = districtAreaLabel(seat.towns);
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
 * by the same grammar the U.S. House section uses.
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

/** The state legislature, or nothing.
 *
 * Renders nothing at all when the state's seats aren't covered — the
 * same call the executive section makes, for the same reason: the
 * page's `omits` list still names them as out of scope, so an empty
 * panel would say it twice and read as a state with no legislature. */
function StateLegislatureSection({ ballot }: { ballot: StateBallot }) {
  if (ballot.stateLegRaces.length === 0) return null;

  return (
    <section className="panel mb-6">
      <TerminalTitlebar title="State legislature" />
      <div className="p-6">
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
    </section>
  );
}

/** The state's own executive officers — Governor, Lieutenant Governor,
 * Attorney General, Secretary of State, Treasurer — where its feed
 * publishes them.
 *
 * Renders nothing at all when the state isn't covered yet. That is the
 * one case where silence is right: the page's `omits` list still names
 * these contests as out of scope, so an empty panel would repeat the
 * same admission twice and, worse, look like a state that elects nobody.
 * The two claims it CAN make — here they are, and this state elects none
 * this cycle — both get real words, same discipline as MeasuresSection.
 *
 * Deliberately much plainer than a federal race: these offices have no
 * FEC filing, so there is no money, no score and nothing to click
 * through to. Showing a name and a party is the whole of what's true.
 */
function StatewideExecutiveSection({ ballot }: { ballot: StateBallot }) {
  const { statewideRaces, statewideCoverage, state } = ballot;
  if (statewideCoverage.status === "not_yet_covered") return null;

  return (
    <section className="panel mb-6">
      <TerminalTitlebar title="State executive" />
      <div className="p-6">
        <h2 className="font-mono text-xs text-ink-lo mb-3">STATEWIDE EXECUTIVE OFFICES</h2>
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
                <span className="font-mono text-xs text-ink-lo sm:self-center">{race.label}</span>
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
          {/* Only meaningful next to actual nominees. Under a confirmed
              absence it explains the funding data missing from names that
              aren't there. */}
          {statewideRaces.length > 0 &&
            " These offices have no federal campaign-finance filings, so no funding figures or Representation Scores exist for them."}
        </p>
      </div>
    </section>
  );
}

/** The measures section, including the three ways it can be empty.
 *
 * The whole point of this component is that "this state has no measures"
 * and "we don't know this state's measures" are different claims. An
 * empty section under a heading like "STATEWIDE BALLOT MEASURES" reads as
 * the first, so a state we simply have not ingested — 17 amendments and
 * all — would silently tell a voter there is nothing to research.
 */
function MeasuresSection({ ballot }: { ballot: StateBallot }) {
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
          . Your county or city may still have local measures — check the official
          lookup above.
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
        . Use the official lookup above to see everything on your ballot.
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
 * content, never a replacement. Renders nothing when the backend has no
 * curated towns for this state (feature unconfigured, or none added yet
 * — see backend/app/data/town_directory.json), same "absence isn't an
 * error" discipline as MeasuresSection above.
 *
 * Resolved against a fixed, public representative address (e.g. town
 * hall) chosen by Civitas, never one a visitor types in — see
 * GOOGLE_CIVIC_API_KEY's comment in config.py for why. That is a real
 * approximation, not a precinct-accurate lookup, and the copy below says
 * so: a town can contain more than one precinct.
 */
function TownSection({ state, pageElectionDate }: { state: string; pageElectionDate: string }) {
  const [towns, setTowns] = useState<TownEntry[] | null>(null);
  const [selected, setSelected] = useState("");
  const [ballot, setBallot] = useState<TownBallot | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchTownsForState(state)
      .then((t) => {
        if (!cancelled) setTowns(t);
      })
      .catch(() => {
        if (!cancelled) setTowns([]);
      });
    return () => {
      cancelled = true;
    };
  }, [state]);

  useEffect(() => {
    if (!selected) {
      setBallot(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
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

  if (!towns || towns.length === 0) return null;

  return (
    <section className="panel mb-6">
      <TerminalTitlebar title="Local races" />
      <div className="p-6">
        <h2 className="font-mono text-xs text-ink-lo mb-1">LOCAL RACES — BY TOWN</h2>
        <p className="text-[11px] text-ink-min mb-3">
          Optional and approximate: results are resolved against a fixed, public address
          in the town you pick (e.g. town hall) — never an address you type in. If your
          own precinct differs from that address within town limits, some local races
          here may not match yours.
        </p>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="bg-surface-base border border-white/15 text-ink-hi font-mono text-xs px-3 py-2 mb-4"
          aria-label="Select your town for local races (optional, approximate)"
        >
          <option value="">— statewide only —</option>
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
                // Load-bearing, not decoration: this source's most
                // recently published ballot can be an EARLIER election
                // (right now, a September primary) than the general
                // election the rest of this page is titled for. Showing
                // those candidates with no warning would misstate what's
                // actually on the November ballot.
                <div className="border border-signal-amber/40 bg-signal-amber/10 p-3">
                  <p className="text-xs text-signal-amber">
                    These local races are from {selected}&apos;s{" "}
                    {ballot.electionName || "most recently published ballot"}
                    {ballot.electionDate ? ` (${ballot.electionDate})` : ""} —{" "}
                    <strong>not</strong> the {pageElectionDate} general election above.{" "}
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
            <p className="text-xs text-ink-lo">
              No local races on file for {selected} this cycle.
            </p>
          )
        )}

        {selected && !loading && ballot?.status === "ingest_failed" && (
          <p className="text-xs text-signal-amber">
            Could not load {selected}&apos;s local races right now — try again shortly.
          </p>
        )}
      </div>
    </section>
  );
}

export default function StateBallotClient({ ballot }: { ballot: StateBallot }) {
  const hasFederalRaces = ballot.senateRaces.length > 0 || ballot.houseRaces.length > 0;
  const { officialLookup } = ballot;
  // officialLookup.url comes from state_ballot_lookup.json via the API,
  // same external-data trust boundary CoverageFeed.tsx guards for article
  // URLs. This is "the one link on the page whose failure strands the
  // visitor" (election_pipeline.py), so on a malformed/unsafe URL this
  // falls back to the same USAGov default the backend itself falls back
  // to (ballot_lookup.py's lookup_for_state), rather than going dead.
  const lookupHref = safeHref(officialLookup.url) || "https://www.usa.gov/election-office";

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <Link
            href="/elections"
            className="inline-block mb-6 font-mono text-xs text-ink-lo hover:text-phos transition-colors"
          >
            ← ALL STATES
          </Link>

          <div className="panel mb-6">
            <TerminalTitlebar title={`${ballot.state.toLowerCase()}-ballot`} />
            <div className="p-6">
              <h1 className="font-display font-semibold text-lg sm:text-2xl text-ink-hi mb-1">
                {ballot.state} — {ballot.cycleYear} GENERAL ELECTION
              </h1>
              <p className="font-mono text-xs text-ink-lo">{ballot.electionDate}</p>
              {/* The primary is read from this state's own election feed,
                  so a state we don't cover simply doesn't get this line —
                  an unknown date is left unknown rather than guessed. */}
              {ballot.primaryDate && (
                <p className="font-mono text-xs text-ink-min mt-0.5">
                  PRIMARY: {ballot.primaryDate}
                </p>
              )}
              {ballot.statePvi !== null && (
                <>
                  <p className={`font-mono text-sm mt-2 ${pviColor(ballot.statePvi)}`}>
                    {formatPvi(ballot.statePvi)}{" "}
                    <span className="font-mono text-xs text-ink-min">statewide lean</span>
                  </p>
                  {/* This is the newest page showing a raw PVI figure — the
                      map and per-race pages already explain it, this one
                      didn't (2026-08 review): "R+12"/"D+8" means nothing
                      to a reader who hasn't seen Cook PVI notation before. */}
                  <PviMethodologyNote />
                </>
              )}
            </div>
          </div>

          {/* Front and center, not one click away on a per-race page —
              a voter's first question is usually "what's being said
              about my ballot", not just "who's on it" (2026-08 review). */}
          <section className="panel mb-6">
            <TerminalTitlebar title="Coverage" />
            <div className="p-6">
              <h2 className="font-mono text-xs text-ink-lo mb-3">NEWS COVERAGE — {ballot.state}</h2>
              <div className="max-h-[420px] overflow-y-auto pr-2">
                <CoverageFeed items={ballot.coverage} />
              </div>
            </div>
          </section>

          {/* Scope + the way out, ABOVE the content rather than in a
              footnote — the page shows a minority of what a voter will
              actually be handed, and burying that under the content is
              how a partial digest gets read as a complete ballot. */}
          <section className="panel mb-6 border-t-2 border-t-signal-cyan/40">
            <div className="p-5">
              <h2 className="font-mono text-xs text-signal-cyan mb-2">
                THIS IS NOT YOUR FULL BALLOT
              </h2>
              <p className="font-sans text-xs text-ink mb-3">
                Ballots are printed per precinct, so most of what you will vote on cannot be
                shown on a statewide page. Not included here:
              </p>
              <ul className="font-sans text-xs text-ink-lo list-disc pl-5 mb-4 space-y-0.5">
                {ballot.omits.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <a
                href={lookupHref}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`${officialLookup.label} (opens in new tab)`}
                className="inline-block font-mono text-[11px] tracking-widest px-3 py-2 border border-signal-cyan/40
                           text-signal-cyan hover:bg-signal-cyan/10 transition-colors"
              >
                {officialLookup.isStateSpecific
                  ? `SEE YOUR FULL ${ballot.state} BALLOT ↗`
                  : "FIND YOUR ELECTION OFFICE ↗"}
              </a>
              <p className="text-[10px] text-ink-min mt-2">
                {officialLookup.label} · {officialLookup.sourceName}
              </p>
            </div>
          </section>

          <section className="panel mb-6">
            <TerminalTitlebar title="Ballot measures" />
            <div className="p-6">
              <h2 className="font-mono text-xs text-ink-lo mb-3">STATEWIDE BALLOT MEASURES</h2>
              <MeasuresSection ballot={ballot} />
            </div>
          </section>

          {ballot.senateRaces.length > 0 && (
            <section className="panel mb-6">
              <TerminalTitlebar title="Senate" />
              <div className="p-6">
                <h2 className="font-mono text-xs text-ink-lo mb-3">U.S. SENATE</h2>
                {/* A state normally has exactly one Senate race per cycle
                    (only one of its two seats' classes is ever up at
                    once) — the ONLY way a second one appears is a
                    special election filling a vacancy alongside the
                    regularly-scheduled race. When that happens, showing
                    both candidate lists under one unlabeled "U.S.
                    SENATE" heading would blend two different seats'
                    options together, so label each race once there's
                    more than one. */}
                {ballot.senateRaces.map((race) => (
                  <div key={race.id} className={ballot.senateRaces.length > 1 ? "mb-5" : ""}>
                    {ballot.senateRaces.length > 1 && (
                      <p className="font-mono text-xs text-signal-cyan mb-2 tracking-widest">
                        {race.isSpecial ? "SPECIAL ELECTION" : "REGULAR ELECTION"}
                      </p>
                    )}
                    <RaceFullDetail race={race} />
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* The Senate's three-class rotation means most states have no
              regular Senate race most cycles — an empty section with no
              explanation reads as missing data, not as "not up yet."
              Only shown when the backend actually has an answer (null
              for DC, which has no Senate seats at all) AND there's real
              House data on the page — otherwise this and the `!hasFederalRaces`
              fallback below aren't mutually exclusive: a state with a
              genuine full data gap (Senate AND House both unsynced) would
              show "next Senate election is in 2028" directly above "no
              federal races on record," two contradictory claims at once.
              Every state always has ≥1 House seat, so a non-empty
              houseRaces here means the gap (if any) is Senate-specific,
              not "we haven't ingested this state yet." */}
          {ballot.senateRaces.length === 0 && ballot.nextSenateElection !== null &&
            ballot.houseRaces.length > 0 && (
            <section className="panel mb-6">
              <TerminalTitlebar title="Senate" />
              <div className="p-6">
                <h2 className="font-mono text-xs text-ink-lo mb-2">U.S. SENATE</h2>
                <p className="text-sm text-ink">
                  Neither of {ballot.state}&apos;s Senate seats is up for election in{" "}
                  {ballot.cycleYear}. Senators serve staggered six-year terms, so a state
                  votes on one of its two seats roughly once every three cycles. This
                  state&apos;s next regular Senate election is in {ballot.nextSenateElection}.
                </p>
              </div>
            </section>
          )}

          {ballot.houseRaces.length > 0 && (
            <HouseSection houseRaces={ballot.houseRaces} />
          )}

          {/* A real ballot runs federal offices first, then the state's
              own general officers, then everything local — so this sits
              between the House and the town selector. */}
          <StatewideExecutiveSection ballot={ballot} />

          <StateLegislatureSection ballot={ballot} />

          <TownSection state={ballot.state} pageElectionDate={ballot.electionDate} />

          {!hasFederalRaces && (
            <p className="text-base text-ink-min">
              No federal races on record for {ballot.state} in {ballot.cycleYear} yet.
            </p>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
