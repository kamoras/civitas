import type { ScoreBreakdownComponent } from "@/types/scoreBreakdown";
import { getScoreBgColor, getScoreColor } from "@/lib/representation";
import MetricTooltip from "@/components/checker/MetricTooltip";
import { Block } from "./ScoreColumn";

/** What a dimension's score is made of: each component the scorer weighed,
 *  its score as a bar, and — on hover or focus — the scorer's own sentence
 *  saying how that number came about. The components and their details are
 *  the score-breakdown API's; nothing is recomputed here. */
export default function ComponentBars({ components }: { components: ScoreBreakdownComponent[] }) {
  const scored = components.filter((c) => c.score != null);
  if (scored.length === 0) return null;
  return (
    <Block label="What the score is made of">
      <ul className="flex flex-col gap-2.5">
        {scored.map((c) => {
          const value = Math.round(c.score as number);
          return (
            <li
              key={c.label}
              className="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_2rem] items-center gap-2.5 text-sm"
            >
              <span className="text-ink-lo">
                <MetricTooltip text={c.detail}>{c.label}</MetricTooltip>
              </span>
              <span className="h-1.5 bg-white/[0.07]" aria-hidden="true">
                <span
                  className={`block h-full ${getScoreBgColor(value)}`}
                  style={{ width: `${Math.max(value, 1.5)}%` }}
                />
              </span>
              <span className={`text-right font-mono ${getScoreColor(value)}`}>{value}</span>
            </li>
          );
        })}
      </ul>
    </Block>
  );
}
