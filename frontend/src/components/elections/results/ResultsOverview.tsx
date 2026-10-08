"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import RaceMap from "@/components/elections/RaceMap";
import LiveUpdates from "@/components/elections/results/LiveUpdates";
import { useMapTextures } from "@/components/elections/results/MapTextures";
import {
  AWAITING_FILL,
  AWAITING_SWATCH,
  FEED_FAILED_FILL,
  FEED_FAILED_SWATCH,
  NO_COUNT_SWATCH,
  NO_COUNT_TEXT,
  POLLS_OPEN_FILL,
  POLLS_OPEN_SWATCH,
  STALE_SWATCH,
  TIED_FILL,
  UNCOVERED_FILL,
  countReadAt,
  feedFailed,
  flipNotShownText,
  flipShown,
  formatEasternTime,
  formatLed,
  isTied,
  ledParty,
  partyTag,
  pollsStillOpen,
  reportingShare,
  seatsLed,
  stateFeedBehind,
  stateShade,
  summarizeState,
  uncountedSenateRaces,
} from "@/lib/results";
import type { ListedSenateRace, LiveRaceResult, LiveResults } from "@/types/election";

const DC_FILL = "rgba(255, 255, 255, 0.06)";

/** A legend key. Every swatch has a faint border, so the near-background
 * fills (UNCOVERED_FILL, AWAITING_FILL) still read as a key and not a gap;
 * `texture` draws the same marks the map lays over that fill. */
function Swatch({ color, texture }: { color: string; texture?: string }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-3 w-4 border border-white/30"
      style={texture ? { background: `${texture}, ${color}` } : { backgroundColor: color }}
    />
  );
}

/** A Senate line's name: "Senate", or "Senate (special)" for the special
 * race of a state listing more than one. */
function senateName(isSpecial: boolean, several: boolean): string {
  return several && isSpecial ? "Senate (special)" : "Senate";
}

/** A state's Senate lines: each counted race, and each race it elects
 * (LiveResults.senateRaces) that the feed gave no count for, regular
 * first. */
function senateRows(
  counted: LiveRaceResult[],
  listed: ListedSenateRace[] | undefined
): { raceId: string; isSpecial: boolean; result: LiveRaceResult | null; several: boolean }[] {
  const uncounted = uncountedSenateRaces(listed, counted);
  const several = counted.length + uncounted.length > 1;
  return [
    ...counted.map((r) => ({ raceId: r.raceId, isSpecial: r.isSpecial, result: r, several })),
    ...uncounted.map((l) => ({ raceId: l.raceId, isSpecial: l.isSpecial, result: null, several })),
  ].sort((a, b) => Number(a.isSpecial) - Number(b.isSpecial));
}

/** "Senate: Jane Roe (D) leads · early" — one Senate race on a directory
 * row, with "early" under half in, as the map draws it fainter. A count
 * the state lists as official still "leads · official count": never a
 * bare "official" beside a name, which reads as a result. "· flip" only
 * while the figures show it (flipShown); a change of party announced
 * earlier that this count doesn't show says so instead. */
function senateLine(r: LiveRaceResult, several: boolean): string {
  const name = senateName(r.isSpecial, several);
  // Announced earlier: said before "tied" and "no votes yet", as every
  // other surface says it, so no line drops what the counter counts.
  const announced = flipNotShownText(r) ? " · flip announced, not in latest count" : "";
  if (isTied(r)) return `${name}: tied${r.official ? " · official count" : ""}${announced}`;
  const lead = r.candidates[0];
  if (!lead || !r.votesCounted)
    return announced
      ? `${name}: no votes in the latest count${announced}`
      : `${name}: no votes yet`;
  const share = reportingShare(r);
  const early = !r.official && share != null && share < 0.5;
  return `${name}: ${lead.name} (${partyTag(lead.party)}) ${
    r.official ? "leads · official count" : "leads"
  }${early ? " · early" : ""}${flipShown(r) ? " · flip" : announced}`;
}

function LedTally({ led }: { led: Record<string, number> }) {
  const others = Object.entries(led)
    .filter(([p]) => p !== "DEM" && p !== "REP")
    // As formatLed orders them: real parties, then leaders with no party given.
    .sort(([a], [b]) => Number(ledParty(a) === null) - Number(ledParty(b) === null));
  return (
    <span className="flex flex-wrap items-baseline gap-x-4 font-display text-3xl font-extrabold tabular-nums">
      <span className="text-dem-blue">D {led.DEM ?? 0}</span>
      <span className="text-rep-red">R {led.REP ?? 0}</span>
      {others.map(([p, n]) => (
        <span key={p} className="text-ind-purple">
          {partyTag(ledParty(p))} {n}
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
  now,
  refreshFailed = false,
}: {
  results: LiveResults;
  states: string[];
  /** The page's clock (useResultsNow): the server's time as of the last
   * answer, never the browser's, which can be far off — one clock for the
   * whole page, read once there. */
  now: number;
  /** The page's own latest refresh failed (useLiveResults' error). The
   * clock stops where it stood (resultsNow), so no feed is newly called
   * behind for what is this page's failure to ask — and nothing here says
   * LIVE or blames a state's feed: every covered state reads NOT
   * REFRESHED, and every count on the map carries the not-live stripe. */
  refreshFailed?: boolean;
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
  // Seats the count on screen shows changing party. A change announced
  // earlier that the latest count no longer shows (a poll whose total fell
  // announces nothing, so it stands on the issue and in the feed) is not
  // one of them; the counter's card names those separately.
  const flips = results.races.filter(flipShown);
  const flipsNotShown = results.races.filter((r) => flipNotShownText(r) != null);
  // A covered state whose feed isn't being read: its latest read failed,
  // or the backend hasn't read it for well over a sync pass (feedBehind —
  // its sync has stopped, whatever the last read said). With no count it
  // is "feed not read", never "no votes yet"; with an older count it is
  // stale, never live.
  const feeds = results.feeds ?? {};
  // Not while this page's own refresh has failed: what it holds is then
  // simply not refreshed, which says nothing about any state's feed.
  const behind = (state: string) =>
    !refreshFailed && live.has(state) && stateFeedBehind(results, state, now);
  const readFailed = (state: string) =>
    !refreshFailed && live.has(state) && (feedFailed(feeds[state]) || behind(state));
  // States whose feed is being read: covered, and its latest read neither
  // failed nor refused (test data, an outage) nor fallen behind. A covered
  // state whose feed couldn't be read is not "read live", which the totals
  // cards claimed while every read of it was refused. States, not races: a
  // state electing both its senators counts once.
  const readLive = (state: string) => live.has(state) && !readFailed(state);
  const liveSenate = [...senateStates].filter(readLive).length;
  const liveRead = [...live].filter(readLive).length;
  // What a stale count's row and map name say about it: when the feed was
  // last tried or last gave a count.
  const staleParts = (state: string): [string, string] => {
    const feed = feeds[state];
    // The state's last good read, or its races' own read time with no
    // record (countReadAt).
    const readAt = countReadAt(results, state);
    const from = readAt ? `count from ${formatEasternTime(readAt)}` : "older count";
    return [
      behind(state)
        ? feed?.checkedAt
          ? `not checked since ${formatEasternTime(feed.checkedAt)}`
          : "not checked since its polls closed"
        : "latest read failed",
      from,
    ];
  };
  const staleNote = (state: string) => staleParts(state).join(" · ").toUpperCase();
  // A covered state still voting: nothing is said about its count — not
  // "no votes yet" — until its last polls close.
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

  const shade = (state: string) => {
    const s = stateShade(
      state,
      byState.get(state) ?? [],
      chamber,
      live.has(state),
      chamber === "H" || senateStates.has(state),
      readFailed(state),
      voting(state),
      results.senateRaces?.[state] ?? []
    );
    // A count this page couldn't refresh is not live either: striped like a
    // stale one (never solid, which passes for live), named for this
    // page's failure, not the feed's.
    const counted = (byState.get(state) ?? []).some((r) => r.office === chamber);
    return refreshFailed && live.has(state) && counted ? { ...s, stale: true } : s;
  };
  // Every fill a stale count is drawn in, so the map has a stale pattern
  // for each (useMapTextures).
  const staleFills = states
    .map((st) => shade(st))
    .filter((s) => s.stale)
    .map((s) => s.fill);
  const { defs, paint } = useMapTextures(staleFills);
  const fill = (state: string) => {
    if (state === "DC") return DC_FILL;
    const s = shade(state);
    return paint(s.fill, s.stale);
  };
  // A count the feed is no longer refreshing says so in its name, as the
  // row's badge does: its colour is who led when it was last read.
  const label = (state: string) => {
    if (state === "DC") return "DC: no voting member of Congress";
    const s = shade(state);
    if (refreshFailed && live.has(state)) {
      const readAt = countReadAt(results, state);
      return `${s.label}; not live, this page couldn't refresh it${
        s.stale && readAt ? `, count from ${formatEasternTime(readAt)}` : ""
      }`;
    }
    return s.stale && !voting(state)
      ? `${s.label}; not live, ${staleParts(state).join(", ")}`
      : s.label;
  };

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
                <Swatch color={TIED_FILL} /> TIED / SPLIT
              </li>
              <li className="flex items-center gap-1.5">
                {/* A Senate race led by an independent (Nebraska's, in 2026)
                    is purple too, not only a House delegation. */}
                <Swatch color="rgba(201,149,255,0.6)" /> OTHER OR UNSTATED PARTY LEADS
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={POLLS_OPEN_FILL} texture={POLLS_OPEN_SWATCH} /> POLLS NOT YET CLOSED
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={AWAITING_FILL} texture={AWAITING_SWATCH} /> NO VOTES YET
              </li>
              <li className="flex items-center gap-1.5">
                {/* A count for the state's other chamber, none for this one. */}
                <Swatch color={AWAITING_FILL} texture={NO_COUNT_SWATCH} />{" "}
                {NO_COUNT_TEXT.toUpperCase()}
              </li>
              <li className="flex items-center gap-1.5">
                <Swatch color={FEED_FAILED_FILL} texture={FEED_FAILED_SWATCH} /> FEED NOT READ
              </li>
              <li className="flex items-center gap-1.5">
                {/* A count no longer refreshed keeps its leader's colour
                    under the stripe. */}
                <Swatch color="rgba(255,137,137,0.6)" texture={STALE_SWATCH} />{" "}
                {refreshFailed ? "NOT LIVE: THIS PAGE COULDN'T REFRESH" : "STALE: NOT REFRESHED"}
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
              getStateLabel={label}
              defs={defs}
            />
          </div>
          <p className="border-t border-white/[0.07] px-4 py-3 text-xs text-ink-min">
            Colour is who leads each state&apos;s own count, not a projection. Fainter means fewer
            than half the precincts or counties are in; solid means the state lists its count as
            official, and a race there still only leads: Civitas calls no race.{" "}
            {chamber === "H"
              ? "For the House, a state is shaded by the party leading the most of its districts, every party compared, and grey when two lead equally many. It stays fainter while any district has under half in, and turns solid only when every district's count is official."
              : "A state electing both its senators is shaded by the party leading more of its two races, grey when two parties lead equally many, and fainter while either race has under half in; with only one of its two races counted, it is shaded by that one, and the other is named as having no count."}{" "}
            Hatched grey means Civitas shows a count for the state&apos;s other chamber&apos;s races
            but none for this one&apos;s, not that no votes are in.{" "}
            {refreshFailed ? (
              <>
                This page couldn&apos;t refresh the count, so none of it is live: stripes over a
                party&apos;s colour mark the count as this page last read it. That says nothing
                about the states&apos; own feeds.
              </>
            ) : (
              <>
                Amber stripes mean Civitas couldn&apos;t read that state&apos;s feed, or hasn&apos;t
                lately: on a dark state, that says nothing about whether counting has started; over
                a party&apos;s colour, the count is the last one read and is not live.
              </>
            )}
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
            Read live in {liveSenate} of the {senateStates.size}{" "}
            {senateStates.size === 1 ? "state" : "states"} electing a senator
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
            of {liveRead} {liveRead === 1 ? "state" : "states"} read live
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
          {flipsNotShown.length > 0 && (
            <p className="mt-2 text-xs text-ink-min">
              Not counted: {flipsNotShown.length}{" "}
              {flipsNotShown.length === 1 ? "change" : "changes"} of party announced earlier that
              the latest count doesn&apos;t show; each race&apos;s card says what its count shows
            </p>
          )}
          {redrawn.length > 0 && (
            <p className="mt-2 text-xs text-ink-min">
              Not counted: House seats in the{" "}
              {redrawn.length === 1 ? "state" : `${redrawn.length} states`} voting on new district
              lines ({redrawn.join(", ")}), which have no previous holder
              {redrawnCounted > 0 &&
                `; ${redrawnCounted} of the districts with a count so far are among them`}
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
            const stillVoting = voting(state);
            const badge = !isLive
              ? { text: "NO FEED", className: "border-white/15 text-ink-min" }
              : refreshFailed
                ? {
                    // This page's failure, not the state's: no LIVE, no
                    // amber, nothing said about its feed.
                    text: "NOT REFRESHED",
                    className: "border-dashed border-white/30 text-ink-lo",
                  }
                : stillVoting
                  ? {
                      // Not "polls open": from midnight on election day,
                      // before any poll opens, this is true too.
                      text: "POLLS NOT CLOSED",
                      className: "border-signal-cyan/50 text-signal-cyan",
                    }
                  : failed
                    ? {
                        text: hasCount ? "STALE" : "FEED NOT READ",
                        // Not LIVE's look with other words: dashed, and
                        // carrying the map's stripe for the same state.
                        className: "border-dashed border-signal-amber/70 text-ink-hi",
                        texture: hasCount ? STALE_SWATCH : FEED_FAILED_SWATCH,
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
                      className={`flex items-center gap-1 border px-1.5 font-mono text-[11px] tracking-[0.1em] ${badge.className}`}
                    >
                      {"texture" in badge && badge.texture && (
                        <span
                          aria-hidden="true"
                          className="inline-block h-2.5 w-2.5"
                          style={{ background: `${badge.texture}, ${FEED_FAILED_FILL}` }}
                        />
                      )}
                      {badge.text}
                    </span>
                  </span>
                  {/* A chamber with no count shown while the other has
                      one: that absence, never "no votes yet". */}
                  {summary.senate.length > 0 ? (
                    // Every Senate race the state holds — a regular and a
                    // special election each get a line, the regular first,
                    // and one with no count shown says so.
                    senateRows(summary.senate, results.senateRaces?.[state]).map((row) => (
                      <span key={row.raceId} className="text-sm text-ink-lo">
                        {row.result
                          ? senateLine(row.result, row.several)
                          : `${senateName(row.isSpecial, true)}: ${
                              refreshFailed ? "no count as this page last read it" : NO_COUNT_TEXT
                            }`}
                      </span>
                    ))
                  ) : (
                    <span className="text-sm text-ink-lo">
                      {senateStates.has(state)
                        ? isLive
                          ? refreshFailed
                            ? "Senate: no count as this page last read it"
                            : hasCount
                              ? `Senate: ${NO_COUNT_TEXT}`
                              : stillVoting
                                ? "Senate: polls not yet closed"
                                : failed
                                  ? behind(state)
                                    ? "Senate: its feed hasn't been read lately"
                                    : "Senate: couldn't read its feed"
                                  : "Senate: no votes yet"
                          : "Senate race: check the state's count"
                        : "No Senate race this year"}
                    </span>
                  )}
                  {summary.house.length > 0 ? (
                    <span className="font-mono text-xs text-ink-min">
                      HOUSE {formatLed(summary.houseLeads)} LEADING
                    </span>
                  ) : (
                    isLive &&
                    hasCount && (
                      <span className="font-mono text-xs text-ink-min">
                        HOUSE: {NO_COUNT_TEXT.toUpperCase()}
                      </span>
                    )
                  )}
                  {failed && hasCount && !stillVoting && (
                    <span className="font-mono text-xs text-signal-amber">{staleNote(state)}</span>
                  )}
                  {refreshFailed && isLive && hasCount && countReadAt(results, state) && (
                    <span className="font-mono text-xs text-ink-min">
                      COUNT FROM{" "}
                      {formatEasternTime(countReadAt(results, state) ?? "").toUpperCase()}
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
