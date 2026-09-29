import Link from "next/link";
import type { President } from "@/types/president";
import { displayScore } from "@/lib/formatting";
import { getPresidentLabel, getScoreColor } from "@/lib/representation";
import { presidentParty } from "./PresidentScorecard";
import { ordinal } from "./format";

const DIMENSIONS = [
  ["publicMandate", "Public Mandate"],
  ["effectiveness", "Effectiveness"],
  ["agencyAlignment", "Agency Alignment"],
  ["historicalLegacy", "Historical Legacy"],
] as const;

/** The sitting president at a glance, above the leaderboard: the
 *  Presidential Score and its four parts, as stored, with the way into the
 *  full scorecard. */
export default function PresidentSummary({ president }: { president: President }) {
  const s = president.score;
  const party = presidentParty(president.party);
  const overall = displayScore(s.overall);
  const href = `/politicians/${president.id}`;
  return (
    <section
      aria-labelledby="sitting-president"
      className={`grid gap-6 border border-white/25 border-t-[3px] bg-surface px-5 py-5 font-sans sm:px-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center ${party.border}`}
    >
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex min-w-0 items-center gap-4">
          <div
            className={`flex h-16 w-14 shrink-0 flex-col items-center justify-center border-2 ${party.border} ${party.text}`}
            aria-hidden="true"
          >
            <span className="font-display text-2xl font-extrabold leading-none">
              {president.number}
            </span>
          </div>
          <div className="min-w-0">
            <h2
              id="sitting-president"
              className="text-2xl font-extrabold leading-tight text-ink-hi"
            >
              {president.name}
            </h2>
            <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
              {ordinal(president.number)} President · {party.label} ·{" "}
              {president.termStart.slice(0, 4)} to present
            </p>
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {DIMENSIONS.map(([key, label]) => {
            const value = s[key];
            return (
              <div
                key={key}
                className="flex flex-col justify-between gap-1 border-t border-white/[0.12] pt-2"
              >
                <dt className="font-mono text-xs uppercase tracking-[0.1em] text-ink-min">
                  {label}
                </dt>
                <dd
                  className={`font-display text-2xl font-extrabold leading-none ${
                    value == null ? "text-ink-min" : getScoreColor(displayScore(value))
                  }`}
                >
                  {value == null ? (
                    <span className="font-mono text-sm font-normal">Not rated yet</span>
                  ) : (
                    displayScore(value)
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      </div>
      <div className="flex flex-col gap-3 border-t border-white/[0.12] pt-5 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
        <p className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
          Presidential Score
        </p>
        {s.dimensionsAvailable === 0 ? (
          <p className="font-display text-2xl font-extrabold text-ink-min">Not yet calculated</p>
        ) : (
          <div className="flex items-baseline gap-3">
            <span
              className={`font-display text-6xl font-extrabold leading-none ${getScoreColor(overall)}`}
            >
              {overall}
            </span>
            <span className={`font-mono text-sm tracking-[0.1em] ${getScoreColor(overall)}`}>
              {getPresidentLabel(overall)}
            </span>
          </div>
        )}
        <Link
          href={href}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 border border-phos/60 bg-phos/10 px-5 py-2.5 font-mono text-sm uppercase tracking-[0.12em] text-phos transition-colors hover:bg-phos/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-phos"
        >
          Open the full scorecard <span aria-hidden="true">&rarr;</span>
        </Link>
      </div>
    </section>
  );
}
