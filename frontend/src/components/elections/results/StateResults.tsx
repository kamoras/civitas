"use client";

import { useEffect, useMemo, useRef } from "react";
import DistrictMap from "@/components/elections/DistrictMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import { HouseResultRow, RaceResultCard } from "@/components/elections/results/RaceResult";
import { feedFailed, formatEasternTime, seatsLed } from "@/lib/results";
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
  error = null,
  lookupHref,
}: {
  ballot: StateBallot;
  results: LiveResults | null;
  /** The last refresh's failure, if any — shown only when there is no
   * count to show instead. */
  error?: string | null;
  lookupHref: string;
}) {
  const races = useMemo(() => results?.races ?? [], [results]);
  const senate = races.filter((r) => r.office === "S");
  const house = useMemo(
    () =>
      races.filter((r) => r.office === "H").sort((a, b) => (a.district ?? 0) - (b.district ?? 0)),
    [races]
  );
  const byDistrict = useMemo(() => {
    const m = new Map<number, LiveRaceResult>();
    for (const r of house) m.set(r.district ?? 0, r);
    return m;
  }, [house]);
  const isLive = !!results?.liveStates.includes(ballot.state);
  const stateName = ballot.stateName ?? ballot.state;
  const led = seatsLed(house);
  const pollsClose = results?.pollsClose?.[ballot.state];
  const feed = results?.feeds?.[ballot.state];
  const failed = feedFailed(feed);
  const now = useNow();

  // The link the page was OPENED with, latched once: the page later writes
  // #race- hashes itself (opening a research contest), and re-reading the
  // hash on every poll scroll-jumped the reader to a count they never asked
  // for (AGENTS.md: params describing how a page was opened are latched).
  const arrival = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (arrival.current === undefined) {
      const match = /^#race-(.+)$/.exec(window.location.hash);
      arrival.current = match ? decodeURIComponent(match[1]) : null;
    }
    const target = arrival.current;
    if (!target || !races.some((r) => r.raceId === target)) return;
    arrival.current = null; // once
    document.getElementById(`result-${target}`)?.scrollIntoView?.({ block: "start" });
  }, [races]);

  if (!results && error) {
    return (
      <section role="alert" className="mb-8 border border-signal-red/40 bg-surface p-4">
        <h2 className="font-display text-lg font-extrabold text-ink-hi">
          The live count couldn&apos;t be loaded
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-lo">
          This page retries every minute.{" "}
          <a
            href={lookupHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-phos hover:underline"
          >
            {stateName}&apos;s election office publishes the count ↗
          </a>
        </p>
      </section>
    );
  }

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
        <h2 className="font-display text-lg font-extrabold text-ink-hi">
          No live count for {stateName} here
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-lo">
          {stateName}&apos;s election office doesn&apos;t publish a results feed this page can read,
          so its count isn&apos;t shown or coloured here.{" "}
          <a
            href={lookupHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-phos hover:underline"
          >
            {stateName}&apos;s election office publishes it ↗
          </a>
        </p>
      </section>
    );
  }

  return (
    <section aria-label={`${stateName} results`} className="mb-10 space-y-5">
      {races.length === 0 &&
        (pollsClose && Date.parse(pollsClose) > now ? (
          <p className="border border-white/[0.09] bg-surface p-4 text-sm text-ink-lo">
            {`${stateName}'s last polls close at ${formatEasternTime(pollsClose)}. Nothing of its count is shown before then.`}
          </p>
        ) : failed && feed ? (
          // Not "hasn't started": a feed that's down or refused says
          // nothing about whether counting has begun.
          <p
            role="status"
            className="border border-signal-amber/40 bg-surface p-4 text-sm text-ink-lo"
          >
            Civitas couldn&apos;t read {stateName}&apos;s results feed (last tried{" "}
            {formatEasternTime(feed.checkedAt)}), so no count is shown here yet. This page keeps
            trying.{" "}
            <a
              href={lookupHref}
              target="_blank"
              rel="noopener noreferrer"
              className="text-phos hover:underline"
            >
              {stateName}&apos;s election office publishes the count ↗
            </a>
          </p>
        ) : (
          <p className="border border-white/[0.09] bg-surface p-4 text-sm text-ink-lo">
            {`${stateName}'s count hasn't started yet. Results appear here as the state publishes them.`}
          </p>
        ))}
      {races.length > 0 && failed && feed && (
        <p role="status" className="font-mono text-xs tracking-[0.06em] text-signal-amber">
          THE LAST READ OF {stateName.toUpperCase()}&apos;S FEED, AT{" "}
          {formatEasternTime(feed.checkedAt).toUpperCase()}, COULDN&apos;T BE USED
          {feed.lastOkAt
            ? ` · THE COUNT BELOW WAS READ AT ${formatEasternTime(feed.lastOkAt).toUpperCase()}`
            : ""}
        </p>
      )}
      {senate.map((r) => (
        <RaceResultCard key={r.raceId} result={r} headingLevel={2} />
      ))}
      {house.length > 0 && (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_26rem]">
          <section
            aria-labelledby="house-results-heading"
            className="min-w-0 border border-white/[0.09] bg-surface"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/[0.09] px-4 py-3">
              <h2
                id="house-results-heading"
                className="font-display text-xl font-extrabold text-ink-hi"
              >
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
                document
                  .getElementById(`result-${raceId}`)
                  ?.scrollIntoView?.({ behavior: "smooth", block: "center" })
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
