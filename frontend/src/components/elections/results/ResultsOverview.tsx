"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import RaceMap from "@/components/elections/RaceMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import { UNCOVERED_FILL, formatEasternTime, partyLetter, seatsLed, stateFill, summarizeState } from "@/lib/results";
import type { LiveRaceResult, LiveResults } from "@/types/election";

const DC_FILL = "rgba(255, 255, 255, 0.06)";

function Swatch({ color, outline = false }: { color: string; outline?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3 w-4"
      style={outline ? { border: `2px solid ${color}` } : { backgroundColor: color }}
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
export default function ResultsOverview({ results, states }: { results: LiveResults; states: string[] }) {
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

  const fill = (state: string) =>
    state === "DC"
      ? DC_FILL
      : stateFill(byState.get(state) ?? [], chamber, live.has(state), chamber === "H" || senateStates.has(state));

  // Covered states first (they have something to show), then the rest.
  const directory = [...states].sort(
    (a, b) => Number(live.has(b)) - Number(live.has(a)) || a.localeCompare(b)
  );

  return (
    <div className="mt-6 space-y-8">
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section aria-labelledby="results-map-heading" className="min-w-0 border border-phos/20 bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-2.5">
            <h2 id="results-map-heading" className="sr-only">
              Results map
            </h2>
            <div role="group" aria-label="Chamber" className="flex font-mono text-xs tracking-[0.12em]">
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
                <Swatch color="rgba(130,172,255,0.3)" /> UNDER HALF IN
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color="#2a2520" /> NO VOTES YET
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={UNCOVERED_FILL} /> {chamber === "S" ? "NO RACE / NO LIVE FEED" : "NO LIVE FEED"}
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
            Colour is who leads each state&apos;s own count, not a projection. Paler means fewer than half the
            precincts or counties are in; solid means the state calls its count official.
            {chamber === "H" && " For the House, a state is shaded by the party leading more of its districts."}
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
          <p className="mt-1 text-sm text-ink-lo">{house.length} districts in {live.size} states read live</p>
        </div>
        <div className={`border bg-surface p-4 ${flips.length ? "border-signal-amber/60" : "border-white/[0.09]"}`}>
          <p className="font-mono text-xs tracking-[0.12em] text-signal-amber">SEATS CHANGING PARTY</p>
          <p className="mt-2 font-display text-3xl font-extrabold tabular-nums text-ink-hi">{flips.length}</p>
          <p className="mt-1 text-sm text-ink-lo">Leader from a different party than the holder, with half or more in</p>
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
            const s = summary.senate[0];
            return (
              <li key={state}>
                <Link
                  href={`/elections/states/${state}`}
                  className="flex h-full flex-col gap-1 bg-surface-base px-3 py-3 transition-colors hover:bg-surface-raised"
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="font-mono text-sm text-ink-hi">{state}</span>
                    <span
                      className={`border px-1.5 font-mono text-[11px] tracking-[0.1em] ${
                        isLive ? "border-signal-amber/50 text-signal-amber" : "border-white/15 text-ink-min"
                      }`}
                    >
                      {isLive ? "LIVE" : "NO FEED"}
                    </span>
                  </span>
                  <span className="text-sm text-ink-lo">
                    {s?.candidates[0] && s.votesCounted
                      ? `Senate: ${s.candidates[0].name} (${partyLetter(s.candidates[0].party)}) ${
                          s.official ? "official" : "leads"
                        }${s.flip ? " · flip" : ""}`
                      : senateStates.has(state)
                        ? isLive
                          ? "Senate: no votes yet"
                          : "Senate race: check the state's count"
                        : "No Senate race this year"}
                  </span>
                  {summary.house.length > 0 && (
                    <span className="font-mono text-xs text-ink-min">
                      HOUSE D {summary.houseLeads.DEM ?? 0} · R {summary.houseLeads.REP ?? 0} LEADING
                    </span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
        <p className="mt-3 max-w-3xl text-sm text-ink-min">
          Live counts come from the {live.size} states whose election offices publish a results feed this page can
          read. For every other state, its own election office publishes the count; each state page links there.
          {results.phase.lastResultChange && (
            <> Last change {formatEasternTime(results.phase.lastResultChange)}.</>
          )}
        </p>
      </section>
    </div>
  );
}
