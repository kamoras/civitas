"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import RaceMap from "@/components/elections/RaceMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import {
  AWAITING_FILL,
  FEED_FAILED_FILL,
  POLLS_OPEN_FILL,
  TIED_FILL,
  UNCOVERED_FILL,
  feedFailed,
  formatEasternTime,
  formatLed,
  isTied,
  partyLetter,
  pollsStillOpen,
  seatsLed,
  stateFill,
  summarizeState,
} from "@/lib/results";
import { useNow } from "@/hooks/useNow";
import type { LiveRaceResult, LiveResults } from "@/types/election";

const DC_FILL = "rgba(255, 255, 255, 0.06)";

/** A legend key. Every swatch has a faint border, so the near-background
 * fills (UNCOVERED_FILL, AWAITING_FILL) still read as a key and not a gap. */
function Swatch({ color }: { color: string }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3 w-4 border border-white/30"
      style={{ backgroundColor: color }}
    />
  );
}

function LedTally({ led }: { led: Record<string, number> }) {
  const others = Object.entries(led).filter(([p]) => p !== "DEM" && p !== "REP");
  return (
    <span className="flex flex-wrap items-baseline gap-x-4 font-display text-3xl font-extrabold tabular-nums">
      <span className="text-dem-blue">D {led.DEM ?? 0}</span>
      <span className="text-rep-red">R {led.REP ?? 0}</span>
      {others.map(([p, n]) => (
        <span key={p} className="text-ind-purple">
          {partyLetter(p)} {n}
        </span>
      ))}
    </span>
  );
}

/**
 * /elections from election day until the results window closes: the live
 * count first, ballot research one click away on every state page.
 *
 * The map shades by who LEADS the count — the state's own numbers, not a
 * projection — and a state this page has no live feed for is drawn as
 * exactly that, never as a state where nothing has happened.
 */
export default function ResultsOverview({
  results,
  states,
}: {
  results: LiveResults;
  states: string[];
}) {
  const router = useRouter();
  const [chamber, setChamber] = useState<"S" | "H">("S");
  const live = useMemo(() => new Set(results.liveStates), [results.liveStates]);
  const senateStates = useMemo(() => new Set(results.senateStates), [results.senateStates]);
  const byState = useMemo(() => {
    const m = new Map<string, LiveRaceResult[]>();
    for (const r of results.races) m.set(r.state, [...(m.get(r.state) ?? []), r]);
    return m;
  }, [results.races]);

  const senate = results.races.filter((r) => r.office === "S");
  const house = results.races.filter((r) => r.office === "H");
  const flips = results.races.filter((r) => r.flip);
  const liveSenate = [...senateStates].filter((s) => live.has(s)).length;
  // A covered state whose latest feed read failed: with no count it is
  // "feed not read", never "no votes yet"; with an older count it is stale.
  const feeds = results.feeds ?? {};
  const readFailed = (state: string) => live.has(state) && feedFailed(feeds[state]);
  // A covered state still voting: nothing is said about its count — not
  // "no votes yet" — until its last polls close.
  const now = useNow();
  const voting = (state: string) => live.has(state) && pollsStillOpen(results, state, now);
  // Districts with a count so far: a row with votes in it. Rows are only
  // the races each state's feed lists, not every district in the state.
  const houseCounted = house.filter((r) => r.votesCounted > 0).length;
  const houseStates = new Set(house.filter((r) => r.votesCounted > 0).map((r) => r.state)).size;
  // States voting on new congressional lines: the live sync gives their
  // House seats no holder, so none can count as changing party. Said on
  // the card, or a quiet count there reads as "no flips".
  const redrawn = [...(results.redrawnStates ?? [])].sort();
  const redrawnSet = new Set(redrawn);
  const redrawnCounted = house.filter((r) => redrawnSet.has(r.state) && r.votesCounted > 0).length;

  const fill = (state: string) =>
    state === "DC"
      ? DC_FILL
      : stateFill(
          byState.get(state) ?? [],
          chamber,
          live.has(state),
          chamber === "H" || senateStates.has(state),
          readFailed(state),
          voting(state)
        );

  // Covered states first (they have something to show), then the rest.
  const directory = [...states].sort(
    (a, b) => Number(live.has(b)) - Number(live.has(a)) || a.localeCompare(b)
  );

  return (
    <div className="mt-6 space-y-8">
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section
          aria-labelledby="results-map-heading"
          className="min-w-0 border border-phos/20 bg-surface"
        >
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-2.5">
            <h2 id="results-map-heading" className="sr-only">
              Results map
            </h2>
            <div
              role="group"
              aria-label="Chamber"
              className="flex font-mono text-xs tracking-[0.12em]"
            >
              {(["S", "H"] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-pressed={chamber === c}
                  onClick={() => setChamber(c)}
                  className={`min-h-11 border px-4 ${
                    chamber === c
                      ? "border-phos bg-surface-raised text-ink-hi"
                      : "border-white/20 text-ink-min hover:text-ink-hi"
                  } ${c === "H" ? "-ml-px" : ""}`}
                >
                  {c === "S" ? "SENATE" : "HOUSE"}
                </button>
              ))}
            </div>
            <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs text-ink-lo">
              <li className="flex items-center gap-1.5">
                <Swatch color="rgba(130,172,255,0.85)" /> D LEADS
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color="rgba(255,137,137,0.85)" /> R LEADS
              </li>
              <li className="flex items-center gap-1.5">
                {/* Either party's colour, fainter: not a blue-only state. */}
                <span className="flex">
                  <Swatch color="rgba(130,172,255,0.3)" />
                  <Swatch color="rgba(255,137,137,0.3)" />
                </span>{" "}
                FAINTER: UNDER HALF IN
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={TIED_FILL} /> {chamber === "S" ? "TIED" : "TIED / SPLIT"}
              </li>
              <li className="flex items-center gap-1.5">
                {/* A Senate race led by an independent (Nebraska's, in 2026)
                    is purple too, not only a House delegation. */}
                <Swatch color="rgba(201,149,255,0.6)" /> OTHER PARTY LEADS
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={POLLS_OPEN_FILL} /> POLLS OPEN
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={AWAITING_FILL} /> NO VOTES YET
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={FEED_FAILED_FILL} /> FEED NOT READ
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={UNCOVERED_FILL} />{" "}
                {chamber === "S" ? "NO RACE / NO LIVE FEED" : "NO LIVE FEED"}
              </li>
            </ul>
          </div>
          <div className="px-4 pt-3">
            <RaceMap
              selectedState={null}
              onStateClick={(state) => {
                if (state !== "DC") router.push(`/elections/states/${state}`);
              }}
              getFillColor={(state) => fill(state)}
              getHoverFillColor={(state) => (state === "DC" ? DC_FILL : fill(state))}
            />
          </div>
          <p className="border-t border-white/[0.07] px-4 py-3 text-xs text-ink-min">
            Colour is who leads each state&apos;s own count, not a projection. Fainter means fewer
            than half the precincts or counties are in; solid means the state calls its count
            official.
            {chamber === "H" &&
              " For the House, a state is shaded by the party leading more of its districts."}{" "}
            Amber means Civitas couldn&apos;t read that state&apos;s feed, which says nothing about
            whether counting has started.
          </p>
        </section>

        <LiveUpdates updates={results.updates} limit={12} />
      </div>

      <section aria-label="Totals" className="grid gap-3 sm:grid-cols-3">
        <div className="border border-white/[0.09] bg-surface p-4">
          <p className="font-mono text-xs tracking-[0.12em] text-ink-min">SENATE SEATS LED</p>
          <div className="mt-2">
            <LedTally led={seatsLed(senate)} />
          </div>
          <p className="mt-1 text-sm text-ink-lo">
            {liveSenate} of {senateStates.size} Senate races read live
          </p>
        </div>
        <div className="border border-white/[0.09] bg-surface p-4">
          <p className="font-mono text-xs tracking-[0.12em] text-ink-min">HOUSE SEATS LED</p>
          <div className="mt-2">
            <LedTally led={seatsLed(house)} />
          </div>
          <p className="mt-1 text-sm text-ink-lo">
            {houseCounted} {houseCounted === 1 ? "district" : "districts"} with a count so far
            {houseCounted > 0 && `, in ${houseStates} ${houseStates === 1 ? "state" : "states"}`},
            of {live.size} states read live
          </p>
        </div>
        <div
          className={`border bg-surface p-4 ${flips.length ? "border-signal-amber/60" : "border-white/[0.09]"}`}
        >
          <p className="font-mono text-xs tracking-[0.12em] text-signal-amber">
            SEATS CHANGING PARTY
          </p>
          <p className="mt-2 font-display text-3xl font-extrabold tabular-nums text-ink-hi">
            {flips.length}
          </p>
          <p className="mt-1 text-sm text-ink-lo">
            Leader from a different party than the holder, with enough of the count in (
            <Link href="/about/elections#election-night" className="text-phos hover:underline">
              what counts as enough
            </Link>
            )
          </p>
          {redrawn.length > 0 && (
            <p className="mt-2 text-xs text-ink-min">
              Not counted: House seats in the{" "}
              {redrawn.length === 1 ? "state" : `${redrawn.length} states`} voting on new district
              lines ({redrawn.join(", ")}), which have no previous holder
              {redrawnCounted > 0 &&
                ` — ${redrawnCounted} of the districts with a count so far are among them`}
              .
            </p>
          )}
        </div>
      </section>

      <section aria-labelledby="results-by-state">
        <h2
          id="results-by-state"
          className="flex items-baseline justify-between border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
        >
          <span>By state</span>
          <span>Ballot research stays on every state page</span>
        </h2>
        <ul className="mt-3 grid gap-px bg-white/[0.07] sm:grid-cols-2 lg:grid-cols-4">
          {directory.map((state) => {
            const summary = summarizeState(byState.get(state) ?? []);
            const isLive = live.has(state);
            const failed = readFailed(state);
            const hasCount = (byState.get(state) ?? []).length > 0;
            const feed = feeds[state];
            const s = summary.senate[0];
            const stillVoting = voting(state);
            const badge = !isLive
              ? { text: "NO FEED", className: "border-white/15 text-ink-min" }
              : stillVoting
                ? { text: "POLLS OPEN", className: "border-signal-cyan/50 text-signal-cyan" }
                : failed
                  ? {
                      text: hasCount ? "STALE" : "FEED NOT READ",
                      className: "border-signal-amber/50 text-signal-amber",
                    }
                  : { text: "LIVE", className: "border-signal-amber/50 text-signal-amber" };
            return (
              <li key={state}>
                <Link
                  href={`/elections/states/${state}`}
                  className="flex h-full flex-col gap-1 bg-surface-base px-3 py-3 transition-colors hover:bg-surface-raised"
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="font-mono text-sm text-ink-hi">{state}</span>
                    <span
                      className={`border px-1.5 font-mono text-[11px] tracking-[0.1em] ${badge.className}`}
                    >
                      {badge.text}
                    </span>
                  </span>
                  <span className="text-sm text-ink-lo">
                    {s && isTied(s)
                      ? `Senate: tied${s.official ? " · official" : ""}`
                      : s?.candidates[0] && s.votesCounted
                        ? `Senate: ${s.candidates[0].name} (${partyLetter(s.candidates[0].party) || "other"}) ${
                            s.official ? "official" : "leads"
                          }${s.flip ? " · flip" : ""}`
                        : senateStates.has(state)
                          ? isLive
                            ? stillVoting
                              ? "Senate: polls still open"
                              : failed && !s
                                ? "Senate: couldn't read its feed"
                                : "Senate: no votes yet"
                            : "Senate race: check the state's count"
                          : "No Senate race this year"}
                  </span>
                  {summary.house.length > 0 && (
                    <span className="font-mono text-xs text-ink-min">
                      HOUSE {formatLed(summary.houseLeads)} LEADING
                    </span>
                  )}
                  {failed && hasCount && (
                    <span className="font-mono text-xs text-signal-amber">
                      {feed?.lastOkAt
                        ? `LATEST READ FAILED · COUNT FROM ${formatEasternTime(feed.lastOkAt).toUpperCase()}`
                        : "LATEST READ FAILED · OLDER COUNT"}
                    </span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
        <p className="mt-3 max-w-3xl text-sm text-ink-min">
          Live counts come from the {live.size} states whose election offices publish a results feed
          this page can read. For every other state, its own election office publishes the count;
          each state page links there.
          {results.phase.lastResultChange && (
            <> Last change {formatEasternTime(results.phase.lastResultChange)}.</>
          )}
        </p>
      </section>
    </div>
  );
}
