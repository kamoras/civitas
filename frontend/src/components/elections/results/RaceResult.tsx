import {
  flipNotShownTag,
  flipNotShownText,
  flipShown,
  formatEasternTime,
  heldByPhrase,
  isTied,
  partyBarColor,
  partyLetter,
  partyTag,
  PARTY_NOT_GIVEN,
  partyTextClass,
  raceLabel,
  reportingShare,
  reportingText,
} from "@/lib/results";
import { safeHref } from "@/lib/formatting";
import type { LiveRaceResult } from "@/types/election";

/** Where the count stands, in the state's own words: leading, tied, or no
 * votes yet — with "official count" once the state lists it so. Never
 * "won", and never a bare "OFFICIAL" beside the leader's name, which reads
 * as a result: an official count's leader still only leads (a Georgia
 * general short of a majority goes to a runoff), and Civitas never calls a
 * race. A seat changing party keeps its FLIP whatever the count's
 * standing — but only while the figures show it (flipShown): a change of
 * party announced earlier that this count no longer shows (a poll whose
 * total fell announces nothing, so the flip stands on the issue and in the
 * feed) says exactly that, never FLIP · LEADING beside the holder's lead
 * — checked before NO VOTES YET, since a count that fell to zero votes is
 * one of them (the overview counts it as "not counted" and points here). */
export function statusTag(r: LiveRaceResult): { text: string; className: string } {
  const notShown = flipNotShownTag(r);
  if (notShown)
    return {
      // Short, as a tag must be; the card's sentence and the maps' names
      // say it in full ("holder's party ahead in the latest count").
      text: `FLIP ANNOUNCED · ${notShown}${r.official ? " · OFFICIAL COUNT" : ""}`,
      className: "border-signal-amber/40 text-ink-hi",
    };
  if (!r.votesCounted) return { text: "NO VOTES YET", className: "border-white/20 text-ink-min" };
  if (isTied(r))
    return {
      text: r.official ? "TIED · OFFICIAL COUNT" : "TIED",
      className: "border-white/40 text-ink-hi",
    };
  if (flipShown(r))
    return {
      text: r.official ? "FLIP · OFFICIAL COUNT" : "FLIP · LEADING",
      className: "border-signal-amber/60 text-signal-amber",
    };
  if (r.official) return { text: "LEADS · OFFICIAL COUNT", className: "border-phos/60 text-phos" };
  const share = reportingShare(r);
  if (share != null && share < 0.5)
    return { text: "EARLY", className: "border-white/20 text-ink-lo" };
  return { text: "LEADING", className: "border-white/25 text-ink-lo" };
}

/** A race's full count: every candidate, a bar each, the reporting line and
 * the source. The Senate race on a state page, or any race when opened. */
export function RaceResultCard({
  result,
  headingLevel = 3,
  newLines = false,
}: {
  result: LiveRaceResult;
  headingLevel?: 2 | 3;
  /** The race's state votes on new congressional lines this cycle
   * (LiveResults.redrawnStates): a House seat there has no previous
   * holder, which the card says rather than leaving "held by" out. */
  newLines?: boolean;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const tag = statusTag(result);
  const reporting = reportingText(result);
  const sourceHref = safeHref(result.sourceUrl);
  return (
    <article
      id={`result-${result.raceId}`}
      aria-labelledby={`result-${result.raceId}-title`}
      // A #race- link lands here, focused (StateResults), and clear of the
      // fixed header, as AboutPage's anchors are.
      tabIndex={-1}
      className="scroll-mt-[var(--header-clearance)] border border-white/[0.09] bg-surface p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Heading
          id={`result-${result.raceId}-title`}
          className="font-display text-xl font-extrabold text-ink-hi"
        >
          {result.office === "S"
            ? `U.S. Senate${result.isSpecial ? " (special)" : ""}`
            : raceLabel(result)}
        </Heading>
        <span className={`border px-2 py-0.5 font-mono text-xs tracking-[0.1em] ${tag.className}`}>
          {tag.text}
        </span>
      </div>
      <p className="mt-1 font-mono text-xs tracking-[0.06em] text-ink-min">
        {[
          reporting,
          `${result.votesCounted.toLocaleString("en-US")} votes`,
          result.heldBy
            ? `held by ${partyLetter(result.heldBy)}`
            : result.office === "H" && newLines
              ? "new district lines · no previous holder"
              : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <ol className="mt-4 space-y-3">
        {result.candidates.map((c, i) => (
          <li
            key={c.candidateId ?? `${i}-${c.name}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 sm:grid-cols-[14rem_minmax(0,1fr)_5rem_7rem]"
          >
            <span className="min-w-0">
              <span className="block truncate font-display text-base font-semibold text-ink-hi">
                {c.name}
              </span>
              <span className={`font-mono text-xs tracking-[0.1em] ${partyTextClass(c.party)}`}>
                {c.party ?? PARTY_NOT_GIVEN.toUpperCase()}
              </span>
            </span>
            <span
              className="order-last col-span-2 h-3 bg-surface-raised sm:order-none sm:col-span-1 sm:h-4"
              aria-hidden="true"
            >
              <span
                className="block h-full"
                style={{ width: `${c.pct ?? 0}%`, backgroundColor: partyBarColor(c.party) }}
              />
            </span>
            <span className="text-right font-display text-lg font-extrabold tabular-nums text-ink-hi">
              {c.pct != null ? `${c.pct.toFixed(1)}%` : "—"}
            </span>
            <span className="hidden text-right font-mono text-xs tabular-nums text-ink-lo sm:block">
              {c.votes.toLocaleString("en-US")}
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-4 text-sm text-ink-lo">
        {[
          !result.votesCounted
            ? flipNotShownText(result)
              ? "No votes in the latest count."
              : "No votes counted yet."
            : isTied(result)
              ? result.official
                ? "Tied in the count the state lists as official; not called."
                : "Tied, not called. The count is not final."
              : result.official
                ? "Leads in the count the state lists as official; not called."
                : "Leading, not called. The count is not final.",
          // Who held the seat, whether or not the count is official: an
          // official count's change of party is still one.
          result.votesCounted && flipShown(result)
            ? `The seat was held by ${heldByPhrase(result.heldBy)}; the leader is from another party.`
            : null,
          // Announced earlier (and still standing on the issue and in the
          // feed), but not what these figures show: say both.
          // With no votes in this count the sentence before says so.
          flipNotShownText(result)
            ? `The seat was held by ${heldByPhrase(result.heldBy)}; a change of party was announced earlier${
                result.votesCounted ? ` (${flipNotShownText(result)})` : ""
              }.`
            : null,
        ]
          .filter(Boolean)
          .join(" ")}{" "}
        {sourceHref ? (
          <a
            href={sourceHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-phos hover:underline"
          >
            {result.sourceName} ↗
          </a>
        ) : (
          <span>{result.sourceName}</span>
        )}
        <span className="text-ink-min"> · read {formatEasternTime(result.fetchedAt)}</span>
      </p>
    </article>
  );
}

/** A House row's grid: two columns on a phone (label, then everything
 * else, the tag wrapping under the leader), four from `sm`. */
const HOUSE_ROW_GRID =
  "scroll-mt-[var(--header-clearance)] grid grid-cols-[3.75rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1 border-b border-white/[0.07] px-4 py-3 last:border-b-0 sm:grid-cols-[4.5rem_minmax(0,1fr)_12rem_8rem]";
/** The status tag's cell: under the leader on a phone, its own column from `sm`. */
const HOUSE_ROW_TAG =
  "col-start-2 justify-self-start sm:col-start-auto sm:justify-self-end sm:text-right";

/** One House district on a line: its leader, a two-party bar, reporting,
 * status. The rows expand nothing — the full count is the card above. */
export function HouseResultRow({ result }: { result: LiveRaceResult }) {
  const tag = statusTag(result);
  const leader = result.candidates[0];
  const tied = isTied(result);
  const total = result.votesCounted || 1;
  const dem = result.candidates.find((c) => c.party === "DEM");
  const rep = result.candidates.find((c) => c.party === "REP");
  return (
    <li
      id={`result-${result.raceId}`}
      // The district map moves focus here when a district is picked, and a
      // #race- link lands here clear of the fixed header.
      tabIndex={-1}
      // On a phone: the label, then the leader in the rest of the width,
      // the status tag on its own line under it. A tag column beside the
      // name left the name 38px at 320px wide — truncated past reading.
      className={HOUSE_ROW_GRID}
    >
      <span className="font-mono text-sm text-ink-hi">{raceLabel(result)}</span>
      <span className="min-w-0">
        <span className="block break-words text-sm text-ink sm:truncate">
          {tied ? (
            // Nobody ahead: both names, neither in a party's lead colour.
            <span className="text-ink-lo">
              Tied:{" "}
              {result.candidates
                .slice(0, 2)
                .map(
                  (c) =>
                    `${c.name} (${partyTag(c.party)}) ${
                      c.pct != null ? `${c.pct.toFixed(1)}%` : "—"
                    }`
                )
                .join(" · ")}
            </span>
          ) : leader && result.votesCounted ? (
            <>
              <span className={partyTextClass(leader.party)}>{leader.name}</span>{" "}
              <span className="text-ink-lo">
                ({partyTag(leader.party)}) {leader.pct != null ? `${leader.pct.toFixed(1)}%` : "—"}
              </span>
            </>
          ) : (
            <span className="text-ink-min">
              {flipNotShownText(result) ? "No votes in the latest count" : "No votes counted yet"}
            </span>
          )}
        </span>
        <span className="mt-1 flex h-1.5 bg-surface-raised" aria-hidden="true">
          <span
            style={{ width: `${(100 * (dem?.votes ?? 0)) / total}%`, backgroundColor: "#82acff" }}
          />
          <span
            className="ml-auto"
            style={{ width: `${(100 * (rep?.votes ?? 0)) / total}%`, backgroundColor: "#ff8989" }}
          />
        </span>
        {/* On a phone the reporting column is folded in here: a lead with
            no "how much is in" beside it reads as a result. */}
        <span className="mt-1 block font-mono text-xs text-ink-min sm:hidden">
          {reportingText(result) || "no reporting figure from the state"}
        </span>
      </span>
      <span className="hidden font-mono text-xs text-ink-min sm:block">
        {reportingText(result) || "—"}
      </span>
      <span
        className={`${HOUSE_ROW_TAG} border px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] ${tag.className}`}
      >
        {tag.text}
      </span>
    </li>
  );
}

/** A House district on the ballot that the state's feed, while counting
 * others, gives no count for: a contest it doesn't list or that couldn't be
 * matched to the race, or an uncontested seat. Listed, so the table is
 * every district and the map has a row to land on, and worded as exactly
 * that absence — not "no votes yet", which says the count is under way. */
export function HouseNoCountRow({
  raceId,
  state,
  district,
}: {
  raceId: string;
  state: string;
  district: number | null;
}) {
  return (
    <li id={`result-${raceId}`} tabIndex={-1} className={HOUSE_ROW_GRID}>
      <span className="font-mono text-sm text-ink-hi">
        {raceLabel({ state, office: "H", district })}
      </span>
      <span className="min-w-0 text-sm text-ink-min">No count from the state&apos;s feed</span>
      <span className="hidden font-mono text-xs text-ink-min sm:block">—</span>
      <span
        className={`${HOUSE_ROW_TAG} border border-dashed border-white/25 px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] text-ink-min`}
      >
        NO COUNT
      </span>
    </li>
  );
}
