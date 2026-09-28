import {
  formatEasternTime,
  partyBarColor,
  partyLetter,
  partyTextClass,
  raceLabel,
  reportingShare,
  reportingText,
} from "@/lib/results";
import type { LiveRaceResult } from "@/types/election";

const HOLDERS: Record<string, string> = { DEM: "Democrats", REP: "Republicans", IND: "an independent" };

/** Where the count stands, in the state's own words: official, leading,
 * or no votes yet. Never "won" without the state saying so. */
export function statusTag(r: LiveRaceResult): { text: string; className: string } {
  if (r.official) return { text: "OFFICIAL", className: "border-phos/60 text-phos" };
  if (!r.votesCounted) return { text: "NO VOTES YET", className: "border-white/20 text-ink-min" };
  if (r.flip) return { text: "FLIP · LEADING", className: "border-signal-amber/60 text-signal-amber" };
  const share = reportingShare(r);
  if (share != null && share < 0.5) return { text: "EARLY", className: "border-white/20 text-ink-lo" };
  return { text: "LEADING", className: "border-white/25 text-ink-lo" };
}

/** A race's full count: every candidate, a bar each, the reporting line and
 * the source. The Senate race on a state page, or any race when opened. */
export function RaceResultCard({ result, headingLevel = 3 }: { result: LiveRaceResult; headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const tag = statusTag(result);
  const reporting = reportingText(result);
  return (
    <article
      id={`result-${result.raceId}`}
      aria-labelledby={`result-${result.raceId}-title`}
      className="border border-white/[0.09] bg-surface p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Heading id={`result-${result.raceId}-title`} className="font-display text-xl font-extrabold text-ink-hi">
          {result.office === "S" ? `U.S. Senate${result.isSpecial ? " (special)" : ""}` : raceLabel(result)}
        </Heading>
        <span className={`border px-2 py-0.5 font-mono text-xs tracking-[0.1em] ${tag.className}`}>{tag.text}</span>
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
        {result.candidates.map((c) => (
          <li
            key={`${c.name}-${c.party}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 sm:grid-cols-[14rem_minmax(0,1fr)_5rem_7rem]"
          >
            <span className="min-w-0">
              <span className="block truncate font-display text-base font-semibold text-ink-hi">{c.name}</span>
              <span className={`font-mono text-xs tracking-[0.1em] ${partyTextClass(c.party)}`}>
                {c.party ?? "OTHER"}
              </span>
            </span>
            <span className="order-last col-span-2 h-3 bg-surface-raised sm:order-none sm:col-span-1 sm:h-4" aria-hidden="true">
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
          : result.flip && result.heldBy
            ? `The leader is from a different party than the ${HOLDERS[result.heldBy] ?? result.heldBy} who hold the seat. The count is not final.`
            : "Leading, not called. The count is not final."}{" "}
        {result.sourceUrl ? (
          <a href={result.sourceUrl} target="_blank" rel="noopener noreferrer" className="text-phos hover:underline">
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
  const total = result.votesCounted || 1;
  const dem = result.candidates.find((c) => c.party === "DEM");
  const rep = result.candidates.find((c) => c.party === "REP");
  return (
    <li
      id={`result-${result.raceId}`}
      className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-white/[0.07] px-4 py-3 last:border-b-0 sm:grid-cols-[4.5rem_minmax(0,1fr)_12rem_8rem]"
    >
      <span className="font-mono text-sm text-ink-hi">{raceLabel(result)}</span>
      <span className="min-w-0">
        <span className="block truncate text-sm text-ink">
          {leader && result.votesCounted ? (
            <>
              <span className={partyTextClass(leader.party)}>{leader.name}</span>{" "}
              <span className="text-ink-lo">
                ({partyLetter(leader.party) || "other"}) {leader.pct?.toFixed(1)}%
              </span>
            </>
          ) : (
            <span className="text-ink-min">No votes counted yet</span>
          )}
        </span>
        <span className="mt-1 flex h-1.5 bg-surface-raised" aria-hidden="true">
          <span style={{ width: `${(100 * (dem?.votes ?? 0)) / total}%`, backgroundColor: "#82acff" }} />
          <span className="ml-auto" style={{ width: `${(100 * (rep?.votes ?? 0)) / total}%`, backgroundColor: "#ff8989" }} />
        </span>
      </span>
      <span className="hidden font-mono text-xs text-ink-min sm:block">{reportingText(result) || "—"}</span>
      <span className={`justify-self-end border px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] ${tag.className}`}>
        {tag.text}
      </span>
    </li>
  );
}
