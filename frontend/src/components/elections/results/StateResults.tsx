"use client";

import { useEffect, useMemo, useRef } from "react";
import DistrictMap from "@/components/elections/DistrictMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import {
  HouseNoCountRow,
  HouseResultRow,
  RaceResultCard,
} from "@/components/elections/results/RaceResult";
import {
  feedFailed,
  formatEasternTime,
  formatLed,
  pollsStillOpen,
  seatsLed,
  showsResults,
} from "@/lib/results";
import { describeInterval, RETRY_BACKOFF_MS } from "@/hooks/useLiveResults";
import { useNow } from "@/hooks/useNow";
import type { LiveRaceResult, LiveResults, StateBallot } from "@/types/election";

/**
 * The top of a state page from election day on: this state's count, read
 * from its own election office, above the ballot research.
 *
 * A #race-{id} link (every election-night Bluesky post carries one) lands
 * on that race's count here rather than on its research drawer. Which of
 * the two it lands on is decided once, by the page (StateBallotClient),
 * when the count first loads; this only scrolls to `arrivalRace`.
 */
export default function StateResults({
  ballot,
  results,
  error = null,
  retryMs = null,
  arrivalRace = null,
  lookupHref,
}: {
  ballot: StateBallot;
  results: LiveResults | null;
  /** The last refresh's failure, if any — shown only when there is no
   * count to show instead. */
  error?: string | null;
  /** The wait before the next retry after a failure (useLiveResults). */
  retryMs?: number | null;
  /** The race a #race- arrival link was handed to the count for, or null
   * (no such link, or it went to research). Decided once by the page. */
  arrivalRace?: string | null;
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
  // Every district on the ballot, in order, with its count or none: a seat
  // the feed gives no count for (unmatched, uncontested) is still a row,
  // saying so, and the map has somewhere to land when it's picked. A count
  // for a district the ballot doesn't list is kept too.
  const houseRows = useMemo(() => {
    const onBallot = new Set(ballot.houseRaces.map((r) => r.district ?? 0));
    const rows: { key: string; district: number; raceId: string; result?: LiveRaceResult }[] =
      ballot.houseRaces.map((r) => ({
        key: r.id,
        district: r.district ?? 0,
        raceId: byDistrict.get(r.district ?? 0)?.raceId ?? r.id,
        result: byDistrict.get(r.district ?? 0),
      }));
    for (const r of house)
      if (!onBallot.has(r.district ?? 0))
        rows.push({ key: r.raceId, district: r.district ?? 0, raceId: r.raceId, result: r });
    return rows.sort((a, b) => a.district - b.district);
  }, [ballot.houseRaces, byDistrict, house]);
  const isLive = !!results?.liveStates.includes(ballot.state);
  const stateName = ballot.stateName ?? ballot.state;
  const led = seatsLed(house);
  const pollsClose = results?.pollsClose?.[ballot.state];
  const feed = results?.feeds?.[ballot.state];
  const failed = feedFailed(feed);
  const now = useNow();
  const stillVoting = !!results && pollsStillOpen(results, ballot.state, now);

  // Scroll to the arrival race once. The page decided, when the count
  // first loaded, that this race has one — so it is on screen by now — and
  // never re-decides on a later poll.
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!arrivalRace || scrolledTo.current === arrivalRace) return;
    scrolledTo.current = arrivalRace;
    document.getElementById(`result-${arrivalRace}`)?.scrollIntoView?.({ block: "start" });
  }, [arrivalRace]);

  if (!results && error) {
    return (
      <section role="alert" className="mb-8 border border-signal-red/40 bg-surface p-4">
        <h2 className="font-display text-lg font-extrabold text-ink-hi">
          The live count couldn&apos;t be loaded
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-lo">
          This page retries {describeInterval(retryMs ?? RETRY_BACKOFF_MS[0])}.{" "}
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

  // The results window closed while the page was open (the backend's phase
  // is back to the campaign): the count is no longer read here, and saying
  // "hasn't started yet" of a finished election would be wrong.
  if (!showsResults(results.phase)) {
    return (
      <section role="status" className="mb-8 border border-white/[0.09] bg-surface p-4">
        <h2 className="font-display text-lg font-extrabold text-ink-hi">
          The live count has ended here
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-lo">
          This election&apos;s results are no longer followed live on this page.{" "}
          <a
            href={lookupHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-phos hover:underline"
          >
            {stateName}&apos;s election office publishes the final count ↗
          </a>
        </p>
      </section>
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
        (stillVoting ? (
          <p className="border border-white/[0.09] bg-surface p-4 text-sm text-ink-lo">
            {pollsClose && Date.parse(pollsClose) > now
              ? `${stateName}'s last polls close at ${formatEasternTime(pollsClose)}. Nothing of its count is shown before then.`
              : `${stateName}'s polls are still open. Nothing of its count is shown before they close.`}
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
      {houseRows.length > 0 && races.length > 0 && (
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
                {formatLed(led)} LEADING
              </span>
            </div>
            <ol>
              {houseRows.map((row) =>
                row.result ? (
                  <HouseResultRow key={row.key} result={row.result} />
                ) : (
                  <HouseNoCountRow
                    key={row.key}
                    raceId={row.raceId}
                    state={ballot.state}
                    district={row.district}
                  />
                )
              )}
            </ol>
          </section>
          <div className="min-w-0 space-y-5">
            <DistrictMap
              state={ballot.state}
              races={ballot.houseRaces}
              picked={null}
              results={byDistrict}
              feedAnswered
              onPick={(raceId) => {
                // Move focus with the scroll, so a keyboard or screen-reader
                // user who picked a district lands on its row.
                const row = document.getElementById(`result-${raceId}`);
                row?.scrollIntoView?.({ behavior: "smooth", block: "center" });
                row?.focus({ preventScroll: true });
              }}
            />
            <LiveUpdates updates={results.updates} limit={8} linkToState={false} />
          </div>
        </div>
      )}
      {!(houseRows.length > 0 && races.length > 0) && results.updates.length > 0 && (
        <LiveUpdates updates={results.updates} limit={8} linkToState={false} />
      )}
    </section>
  );
}
