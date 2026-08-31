"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import RaceFullDetail from "@/components/elections/RaceFullDetail";
import CoverageFeed, { useMounted } from "@/components/elections/CoverageFeed";
import AddressLookup from "@/components/elections/AddressLookup";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import BallotMeasureCard from "@/components/elections/BallotMeasureCard";
import TownContestCard from "@/components/elections/TownContestCard";
import { districtCountiesLabel, formatPvi, pviColor, raceShortLabel } from "@/lib/elections";
import { safeHref } from "@/lib/formatting";
import { fetchTownBallot, fetchTownsForState } from "@/lib/api";
import type { StateBallot, TownBallot, TownEntry } from "@/types/election";

function HouseSection({
  state,
  houseRaces,
}: {
  state: string;
  houseRaces: StateBallot["houseRaces"];
}) {
  const [selectedId, setSelectedId] = useState("");

  // A House race only renders once selected, so a #race-{id} deep link
  // (old /elections/{raceId} redirects, and new Bluesky post links) needs
  // to select it before the browser's anchor-scroll would have any
  // element to find. SSR always renders unselected (window is undefined
  // there), so the hash is only read once mounted — same
  // useSyncExternalStore idiom CoverageFeed uses to avoid a hydration
  // mismatch, here avoiding a setState-in-effect too.
  const mounted = useMounted();
  const hashRaceId = mounted ? (window.location.hash.match(/^#race-(.+)$/)?.[1] ?? null) : null;
  const selected = houseRaces.find((r) => r.id === (selectedId || hashRaceId)) || null;

  useEffect(() => {
    if (selected) {
      document.getElementById(`race-${selected.id}`)?.scrollIntoView();
    }
  }, [selected]);

  return (
    <section className="panel mb-6">
      <TerminalTitlebar title="House" />
      <div className="p-6">
        <h2 className="font-mono text-xs text-ink-lo mb-1">
          U.S. HOUSE — {houseRaces.length} {houseRaces.length === 1 ? "DISTRICT" : "DISTRICTS"}
        </h2>
        {/* You vote in exactly one of these. The address lookup below is
            optional and resolve-only (never stored) — entering your
            address is not required; the dropdown works on its own. */}
        <p className="text-xs text-ink-min mb-3">
          You vote in exactly one of these. Enter your address below to find it automatically, or
          pick it from the dropdown, or{" "}
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
        <AddressLookup
          ballotState={state}
          onResolved={(district) => {
            const match = houseRaces.find((r) => r.district === district);
            if (match) setSelectedId(match.id);
            return match != null;
          }}
        />
        <select
          value={selected?.id ?? ""}
          onChange={(e) => setSelectedId(e.target.value)}
          className="bg-surface-base border border-white/15 text-ink-hi font-mono text-xs px-3 py-2 mb-4"
          aria-label="Select your district"
        >
          <option value="">— select your district —</option>
          {houseRaces.map((r) => {
            const countiesLabel = districtCountiesLabel(r.counties);
            return (
              <option key={r.id} value={r.id}>
                {raceShortLabel(r)}
                {countiesLabel ? ` — ${countiesLabel}` : ""}
              </option>
            );
          })}
        </select>

        {selected && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono text-xs text-ink-hi">{raceShortLabel(selected)}</span>
              <span className={`font-mono text-xs ${pviColor(selected.pvi)}`}>
                {formatPvi(selected.pvi)}
                {selected.pviLevel === "state" && (
                  <span className="ml-1 text-ink-min">(statewide lean)</span>
                )}
              </span>
            </div>
            {selected.counties && (
              <p className="text-xs text-ink-min mb-3">Covers: {selected.counties.join(", ")}</p>
            )}
            <RaceFullDetail race={selected} />
          </div>
        )}
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

          {ballot.houseRaces.length > 0 && (
            <HouseSection state={ballot.state} houseRaces={ballot.houseRaces} />
          )}

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
