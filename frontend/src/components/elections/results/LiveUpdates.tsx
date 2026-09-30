import Link from "next/link";
import { describeUpdate, formatEasternTime, raceLabel } from "@/lib/results";
import type { ResultEvent } from "@/types/election";

const TONE: Record<string, string> = {
  flip: "border-signal-amber/60 text-signal-amber",
  lead: "border-signal-cyan/50 text-signal-cyan",
  official: "border-phos/60 text-phos",
  neutral: "border-white/20 text-ink-lo",
};

/**
 * The night as it happened: first returns, changes of leader, seats
 * changing party, counts the state lists as official — newest first, each
 * worded from the state's own figures (lib/results describeUpdate).
 *
 * Not an aria-live region: a list that grows every few minutes would
 * interrupt a screen-reader user mid-sentence all night. The page's
 * "updated" line is the polite announcement instead.
 */
export default function LiveUpdates({
  updates,
  limit,
  linkToState = true,
  className = "",
}: {
  updates: ResultEvent[];
  limit?: number;
  linkToState?: boolean;
  className?: string;
}) {
  const shown = limit ? updates.slice(0, limit) : updates;
  return (
    <section
      aria-labelledby="live-updates-heading"
      className={`border border-white/[0.09] bg-surface ${className}`}
    >
      <div className="flex items-baseline justify-between border-b border-white/[0.09] px-4 py-3">
        <h2 id="live-updates-heading" className="font-display text-lg font-extrabold text-ink-hi">
          Live updates
        </h2>
        <span className="font-mono text-xs tracking-[0.1em] text-ink-min">NEWEST FIRST</span>
      </div>
      {shown.length === 0 ? (
        <p className="px-4 py-4 text-sm text-ink-lo">
          Nothing yet. Updates appear here as states publish their first counts.
        </p>
      ) : (
        <ol>
          {shown.map((event) => {
            const { tag, tone, text } = describeUpdate(event);
            const label = raceLabel(event);
            return (
              <li key={event.id} className="border-b border-white/[0.07] px-4 py-3 last:border-b-0">
                <p className="flex flex-wrap items-center gap-2 font-mono text-xs tracking-[0.08em]">
                  <time dateTime={event.at} className="text-ink-min">
                    {formatEasternTime(event.at)}
                  </time>
                  <span className={`border px-1.5 ${TONE[tone]}`}>{tag}</span>
                  {linkToState ? (
                    <Link
                      href={`/elections/states/${event.state}#race-${event.raceId}`}
                      className="text-ink-lo underline-offset-2 hover:text-phos hover:underline"
                    >
                      {label}
                    </Link>
                  ) : (
                    <span className="text-ink-lo">{label}</span>
                  )}
                </p>
                <p className="mt-1 text-sm text-ink">{text}</p>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
