"use client";

import { useEffect, useMemo, useRef } from "react";
import DistrictMap from "@/components/elections/DistrictMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import {
  HouseNoCountRow,
  HouseResultRow,
  RaceResultCard,
  SenateNoCountCard,
} from "@/components/elections/results/RaceResult";
import {
  countReadAt as readAtOf,
  feedFailed,
  formatEasternTime,
  formatLed,
  pollsStillOpen,
  serverTimeOf,
  seatsLed,
  showsResults,
  stateFeedBehind,
} from "@/lib/results";
import { describeInterval, RESULTS_POLL_MS, RETRY_BACKOFF_MS } from "@/hooks/useLiveResults";
import type { LiveRaceResult, LiveResults, StateBallot } from "@/types/election";

/**
 * The top of a state page from election day on: this state's count, read
 * from its own election office, above the ballot research.
 *
 * A #race-{id} link (every election-night post carries one) lands
 * on that race's count here rather than on its research drawer. Which of
 * the two it lands on is decided once, by the page (StateBallotClient),
 * when the count first loads; this only scrolls to `arrivalRace`.
 */
export default function StateResults({
  ballot,
  results,
  error = null,
  retryMs = null,
  failedAt = null,
  now,
  arrivalRace = null,
  lookupHref,
  lookupIsStateSpecific,
}: {
  ballot: StateBallot;
  results: LiveResults | null;
  /** The last refresh's failure, if any: in place of the count when there
   * is none, and otherwise on the refresh line above the count, which
   * then says the count shown is an older one. */
  error?: string | null;
  /** The wait before the next retry after a failure (useLiveResults). */
  retryMs?: number | null;
  /** When the last refresh failed, ms since epoch by the browser's clock
   * (useLiveResults); said on the server's clock (serverTimeOf). */
  failedAt?: number | null;
  /** The page's clock (useResultsNow): the server's time as of the last
   * answer, stopped where it stood while refreshes fail — not the
   * browser's clock, and not blaming the feeds for this page's failure to
   * ask. */
  now: number;
  /** The race a #race- arrival link was handed to the count for, or null
   * (no such link, or it went to research). Decided once by the page. */
  arrivalRace?: string | null;
  lookupHref: string;
  /** False when lookupHref is USAGov's national directory of election
   * offices, not the state's own site: the link then says it finds the
   * office rather than promising the state's count is at the other end. */
  lookupIsStateSpecific: boolean;
}) {
  const races = useMemo(() => results?.races ?? [], [results]);
  // Every Senate race on the ballot, with its count or none: once the
  // state's feed is answering, one with no count shown (both contests
  // unpaired, one dropped or unmatched) is a card saying so, as a House
  // district with none is a row. A count for a race the ballot doesn't
  // list is kept too.
  const senateRows = useMemo(() => {
    const counted = races.filter((r) => r.office === "S");
    const byId = new Map(counted.map((r) => [r.raceId, r]));
    const rows: { raceId: string; isSpecial: boolean; result?: LiveRaceResult }[] = [];
    if (races.length > 0)
      for (const r of ballot.senateRaces)
        rows.push({ raceId: r.id, isSpecial: r.isSpecial, result: byId.get(r.id) });
    const listed = new Set(rows.map((r) => r.raceId));
    for (const r of counted)
      if (!listed.has(r.raceId)) rows.push({ raceId: r.raceId, isSpecial: r.isSpecial, result: r });
    return rows;
  }, [races, ballot.senateRaces]);
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
  // with no count shown (unmatched, uncontested, set aside) is still a row,
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
  // Either response can say so; an older backend sends neither.
  const newLines =
    (ballot.newDistrictLines ?? false) || (results?.redrawnStates ?? []).includes(ballot.state);
  const led = seatsLed(house);
  const pollsClose = results?.pollsClose?.[ballot.state];
  const feed = results?.feeds?.[ballot.state];
  // The backend hasn't read the state's feed for well over a sync pass (or
  // has no record of reading it since its polls closed): its sync has
  // stopped, so the count below is not live whatever the last read said —
  // the same rule that badges the state STALE on /elections.
  const behind = !!results && stateFeedBehind(results, ballot.state, now);
  const failed = feedFailed(feed) || behind;
  const stillVoting = !!results && pollsStillOpen(results, ballot.state, now);

  // When the count on screen was read from the state's feed (countReadAt
  // in lib/results: never the page's own clock).
  const countReadAt = results ? readAtOf(results, ballot.state) : null;

  // Land on the arrival race once — scrolled to, and focused, so a keyboard
  // or screen-reader user starts there too rather than at the top of the
  // document. The page decided, when the count first loaded, that this
  // race has one — so it is on screen by now — and never re-decides on a
  // later poll.
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!arrivalRace || scrolledTo.current === arrivalRace) return;
    scrolledTo.current = arrivalRace;
    const target = document.getElementById(`result-${arrivalRace}`);
    target?.scrollIntoView?.({ block: "start" });
    target?.focus({ preventScroll: true });
  }, [arrivalRace]);

  if (!results && error) {
    return (
      <section role="alert" className="mb-8 border border-signal-red/40 bg-surface p-4">
        <h2 className="font-display text-lg font-extrabold text-ink-hi">
          The live count couldn&apos;t be loaded
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-ink-lo">
          This page retries {describeInterval(retryMs ?? RETRY_BACKOFF_MS[0])}.{" "}
          <OfficeLink
            href={lookupHref}
            stateName={stateName}
            stateSpecific={lookupIsStateSpecific}
            what="the count"
          />
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
          <OfficeLink
            href={lookupHref}
            stateName={stateName}
            stateSpecific={lookupIsStateSpecific}
            what="the final count"
          />
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
          <OfficeLink
            href={lookupHref}
            stateName={stateName}
            stateSpecific={lookupIsStateSpecific}
            what="it"
          />
        </p>
      </section>
    );
  }

  return (
    <section aria-label={`${stateName} results`} className="mb-10 space-y-5">
      {/* How current the count on screen is, and whether this page is
          still managing to refresh it: a count that stopped refreshing
          must not pass for a live one. The live region is the state alone
          — refresh failed, stale, live, polls closed — so a screen reader
          hears each change of state once, not every pass's new time; the
          times sit beside it, outside the region. It is also the polite
          announcement for the live-updates feed below, which is
          deliberately not live itself. */}
      <p
        className={`flex flex-wrap gap-x-2 font-mono text-xs tracking-[0.08em] ${
          error || (failed && races.length > 0) ? "text-signal-amber" : "text-ink-min"
        }`}
      >
        <span role="status">
          {error
            ? "REFRESH FAILED"
            : stillVoting
              ? "POLLS NOT YET CLOSED"
              : failed
                ? behind
                  ? "STALE"
                  : "FEED READ FAILED"
                : races.length > 0
                  ? "LIVE"
                  : "POLLS CLOSED"}
        </span>{" "}
        <span>
          {"· "}
          {error
            ? [
                failedAt != null
                  ? `AT ${formatEasternTime(new Date(serverTimeOf(results, failedAt)).toISOString()).toUpperCase()}`
                  : null,
                races.length > 0
                  ? countReadAt
                    ? `SHOWING THE COUNT READ AT ${formatEasternTime(countReadAt).toUpperCase()}`
                    : "SHOWING AN OLDER COUNT"
                  : null,
                `RETRYING ${describeInterval(retryMs ?? RETRY_BACKOFF_MS[0]).toUpperCase()}`,
              ]
                .filter(Boolean)
                .join(" · ")
            : [
                countReadAt && !failed
                  ? `UPDATED ${formatEasternTime(countReadAt).toUpperCase()}`
                  : null,
                `THIS PAGE CHECKS ${describeInterval(RESULTS_POLL_MS).toUpperCase()}`,
              ]
                .filter(Boolean)
                .join(" · ")}
        </span>
      </p>
      {races.length === 0 &&
        (stillVoting ? (
          <p className="border border-white/[0.09] bg-surface p-4 text-sm text-ink-lo">
            {pollsClose && Date.parse(pollsClose) > now
              ? `${stateName}'s last polls close at ${formatEasternTime(pollsClose)}. Nothing of its count is shown before then.`
              : `${stateName}'s polls haven't closed yet. Nothing of its count is shown before they do.`}
          </p>
        ) : failed ? (
          // Not "hasn't started": a feed that's down or refused says
          // nothing about whether counting has begun.
          <p className="border border-signal-amber/40 bg-surface p-4 text-sm text-ink-lo">
            {behind ? (
              feed?.checkedAt ? (
                <>
                  Civitas hasn&apos;t read {stateName}&apos;s results feed since{" "}
                  {formatEasternTime(feed.checkedAt)}, so no count is shown here.{" "}
                </>
              ) : (
                <>
                  Civitas has no record of reading {stateName}&apos;s results feed since its polls
                  closed, so no count is shown here.{" "}
                </>
              )
            ) : (
              <>
                Civitas couldn&apos;t read {stateName}&apos;s results feed (last tried{" "}
                {formatEasternTime(feed?.checkedAt ?? "")}), so no count is shown here yet. This
                page keeps trying.{" "}
              </>
            )}
            <OfficeLink
              href={lookupHref}
              stateName={stateName}
              stateSpecific={lookupIsStateSpecific}
              what="the count"
            />
          </p>
        ) : (
          <p className="border border-white/[0.09] bg-surface p-4 text-sm text-ink-lo">
            {`${stateName}'s count hasn't started yet. Results appear here as the state publishes them.`}
          </p>
        ))}
      {/* Not while this page's own refreshes fail: the line above then
          says so, and when the count was read — the only statement. */}
      {races.length > 0 && failed && !error && (
        <p className="font-mono text-xs tracking-[0.06em] text-signal-amber">
          {behind ? (
            feed?.checkedAt ? (
              <>
                {stateName.toUpperCase()}&apos;S FEED HASN&apos;T BEEN CHECKED SINCE{" "}
                {formatEasternTime(feed.checkedAt).toUpperCase()}
              </>
            ) : (
              <>
                NO RECORD OF {stateName.toUpperCase()}&apos;S FEED BEING CHECKED SINCE ITS POLLS
                CLOSED
              </>
            )
          ) : (
            <>
              THE LAST READ OF {stateName.toUpperCase()}&apos;S FEED, AT{" "}
              {formatEasternTime(feed?.checkedAt ?? "").toUpperCase()}, COULDN&apos;T BE USED
            </>
          )}
          {countReadAt
            ? ` · THE COUNT BELOW WAS READ AT ${formatEasternTime(countReadAt).toUpperCase()}`
            : ""}
        </p>
      )}
      {senateRows.map((row) =>
        row.result ? (
          <RaceResultCard
            key={row.raceId}
            result={row.result}
            headingLevel={2}
            newLines={newLines}
          />
        ) : (
          <SenateNoCountCard
            key={row.raceId}
            raceId={row.raceId}
            isSpecial={row.isSpecial}
            headingLevel={2}
          />
        )
      )}
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
            {newLines && (
              // The live sync gives these seats no holder (heldBy null),
              // so none is ever marked FLIP; say why, or the absence of
              // flips in a state that redrew reads as none happening.
              <p className="border-b border-white/[0.09] px-4 py-2 text-xs text-ink-min">
                New district lines this year: no seat has a previous holder, so none is marked as
                changing party.
              </p>
            )}
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
              // Striped too while this page's own refresh fails: an old
              // count never passes for a live one. (Its legend says "the
              // last count read, not live" — no blame on the feed.)
              stale={failed || !!error}
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

/**
 * Where the state's own count is published. Worded by what the link really
 * is: the state's election office, or USAGov's directory of them — the
 * latter doesn't show a count, it finds the office that does.
 */
function OfficeLink({
  href,
  stateName,
  stateSpecific,
  what,
}: {
  href: string;
  stateName: string;
  stateSpecific: boolean;
  what: "the count" | "the final count" | "it";
}) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-phos hover:underline">
      {stateSpecific
        ? `${stateName}'s election office publishes ${what} ↗`
        : `Find ${stateName}'s election office, which publishes ${what} (USAGov directory) ↗`}
    </a>
  );
}
