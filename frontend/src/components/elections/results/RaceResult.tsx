import {
  formatEasternTime,
  heldByPhrase,
  isTied,
  partyBarColor,
  partyLetter,
  partyTextClass,
  raceLabel,
  reportingShare,
  reportingText,
} from "@/lib/results";
import { safeHref } from "@/lib/formatting";
import type { LiveRaceResult } from "@/types/election";

/** Where the count stands, in the state's own words: official, leading,
 * tied, or no votes yet. Never "won" without the state saying so. */
export function statusTag(r: LiveRaceResult): { text: string; className: string } {
  if (r.official) return { text: "OFFICIAL", className: "border-phos/60 text-phos" };
  if (!r.votesCounted) return { text: "NO VOTES YET", className: "border-white/20 text-ink-min" };
  if (isTied(r)) return { text: "TIED", className: "border-white/40 text-ink-hi" };
  if (r.flip)
    return { text: "FLIP · LEADING", className: "border-signal-amber/60 text-signal-amber" };
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
}: {
  result: LiveRaceResult;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const tag = statusTag(result);
  const reporting = reportingText(result);
  const sourceHref = safeHref(result.sourceUrl);
  return (
    <article
      id={`result-${result.raceId}`}
      aria-labelledby={`result-${result.raceId}-title`}
      // A #race- link lands here: clear of the fixed header, as AboutPage's
      // anchors are.
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
          result.heldBy ? `held by ${partyLetter(result.heldBy)}` : null,
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
                {c.party ?? "OTHER"}
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
        {result.official
          ? "The state lists this count as official."
          : isTied(result)
            ? "Tied, not called. The count is not final."
            : result.flip && result.heldBy
              ? `The seat was held by ${heldByPhrase(result.heldBy)}; the leader is from another party. The count is not final.`
              : "Leading, not called. The count is not final."}{" "}
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
      className="scroll-mt-[var(--header-clearance)] grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-white/[0.07] px-4 py-3 last:border-b-0 sm:grid-cols-[4.5rem_minmax(0,1fr)_12rem_8rem]"
    >
      <span className="font-mono text-sm text-ink-hi">{raceLabel(result)}</span>
      <span className="min-w-0">
        <span className="block truncate text-sm text-ink">
          {tied ? (
            // Nobody ahead: both names, neither in a party's lead colour.
            <span className="text-ink-lo">
              Tied:{" "}
              {result.candidates
                .slice(0, 2)
                .map(
                  (c) =>
                    `${c.name} (${partyLetter(c.party) || "other"}) ${
                      c.pct != null ? `${c.pct.toFixed(1)}%` : "—"
                    }`
                )
                .join(" · ")}
            </span>
          ) : leader && result.votesCounted ? (
            <>
              <span className={partyTextClass(leader.party)}>{leader.name}</span>{" "}
              <span className="text-ink-lo">
                ({partyLetter(leader.party) || "other"}){" "}
                {leader.pct != null ? `${leader.pct.toFixed(1)}%` : "—"}
              </span>
            </>
          ) : (
            <span className="text-ink-min">No votes counted yet</span>
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
      </span>
      <span className="hidden font-mono text-xs text-ink-min sm:block">
        {reportingText(result) || "—"}
      </span>
      <span
        className={`justify-self-end border px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] ${tag.className}`}
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
    <li
      id={`result-${raceId}`}
      tabIndex={-1}
      className="scroll-mt-[var(--header-clearance)] grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-white/[0.07] px-4 py-3 last:border-b-0 sm:grid-cols-[4.5rem_minmax(0,1fr)_12rem_8rem]"
    >
      <span className="font-mono text-sm text-ink-hi">
        {raceLabel({ state, office: "H", district })}
      </span>
      <span className="min-w-0 text-sm text-ink-min">No count from the state&apos;s feed</span>
      <span className="hidden font-mono text-xs text-ink-min sm:block">—</span>
      <span className="justify-self-end border border-dashed border-white/25 px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] text-ink-min">
        NO COUNT
      </span>
    </li>
  );
}
