"use client";

import { useEffect, useMemo } from "react";
import DistrictMap from "@/components/elections/DistrictMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import { HouseResultRow, RaceResultCard } from "@/components/elections/results/RaceResult";
import { formatEasternTime, seatsLed } from "@/lib/results";
import { useNow } from "@/hooks/useNow";
import type { LiveRaceResult, LiveResults, StateBallot } from "@/types/election";

/**
 * The top of a state page from election day on: this state's count, read
 * from its own election office, above the ballot research.
 *
 * A #race-{id} link (every election-night Bluesky post carries one) lands
 * on that race's count here rather than on its research drawer.
 */
export default function StateResults({
  ballot,
  results,
  lookupHref,
}: {
  ballot: StateBallot;
  results: LiveResults | null;
  lookupHref: string;
}) {
  const races = useMemo(() => results?.races ?? [], [results]);
  const senate = races.filter((r) => r.office === "S");
  const house = races.filter((r) => r.office === "H").sort((a, b) => (a.district ?? 0) - (b.district ?? 0));
  const byDistrict = useMemo(() => {
    const m = new Map<number, LiveRaceResult>();
    for (const r of house) m.set(r.district ?? 0, r);
    return m;
  }, [house]);
  const isLive = !!results?.liveStates.includes(ballot.state);
  const stateName = ballot.stateName ?? ballot.state;
  const led = seatsLed(house);
  const pollsClose = results?.pollsClose?.[ballot.state];
  const now = useNow();

  useEffect(() => {
    const match = /^#race-(.+)$/.exec(window.location.hash);
    if (!match || !races.length) return;
    document.getElementById(`result-${decodeURIComponent(match[1])}`)?.scrollIntoView?.({ block: "start" });
  }, [races.length]);

  if (!results) {
    return (
      <p role="status" className="mb-6 font-mono text-sm tracking-[0.12em] text-ink-min">
        READING THE COUNT…
      </p>
    );
  }

  if (!isLive) {
    return (
      <section className="mb-8 border border-white/[0.09] bg-surface p-4">
        <h2 className="font-display text-lg font-extrabold text-ink-hi">No live count for {stateName} here</h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-lo">
          {stateName}&apos;s election office doesn&apos;t publish a results feed this page can read, so its count
          isn&apos;t shown or coloured here.{" "}
          <a href={lookupHref} target="_blank" rel="noopener noreferrer" className="text-phos hover:underline">
            {stateName}&apos;s election office publishes it ↗
          </a>
        </p>
      </section>
    );
  }

  return (
    <section aria-label={`${stateName} results`} className="mb-10 space-y-5">
      {races.length === 0 && (
        <p className="border border-white/[0.09] bg-surface p-4 text-sm text-ink-lo">
          {pollsClose && Date.parse(pollsClose) > now
            ? `${stateName}'s last polls close at ${formatEasternTime(pollsClose)}. Nothing of its count is shown before then.`
            : `${stateName}'s count hasn't started yet. Results appear here as the state publishes them.`}
        </p>
      )}
      {senate.map((r) => (
        <RaceResultCard key={r.raceId} result={r} headingLevel={2} />
      ))}
      {house.length > 0 && (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_26rem]">
          <section aria-labelledby="house-results-heading" className="min-w-0 border border-white/[0.09] bg-surface">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/[0.09] px-4 py-3">
              <h2 id="house-results-heading" className="font-display text-xl font-extrabold text-ink-hi">
                U.S. House
              </h2>
              <span className="font-mono text-xs tracking-[0.1em] text-ink-lo">
                D {led.DEM ?? 0} · R {led.REP ?? 0} LEADING
              </span>
            </div>
            <ol>
              {house.map((r) => (
                <HouseResultRow key={r.raceId} result={r} />
              ))}
            </ol>
          </section>
          <div className="min-w-0 space-y-5">
            <DistrictMap
              state={ballot.state}
              races={ballot.houseRaces}
              picked={null}
              results={byDistrict}
              onPick={(raceId) =>
                document.getElementById(`result-${raceId}`)?.scrollIntoView?.({ behavior: "smooth", block: "center" })
              }
            />
            <LiveUpdates updates={results.updates} limit={8} linkToState={false} />
          </div>
        </div>
      )}
      {house.length === 0 && results.updates.length > 0 && (
        <LiveUpdates updates={results.updates} limit={8} linkToState={false} />
      )}
    </section>
  );
}
