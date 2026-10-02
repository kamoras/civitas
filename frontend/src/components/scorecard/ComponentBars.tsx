import type { ScoreBreakdownComponent } from "@/types/scoreBreakdown";
import { getScoreBgColor, getScoreColor } from "@/lib/representation";
import MetricTooltip from "@/components/checker/MetricTooltip";
import { Block } from "./ScoreColumn";

/** What a dimension's score is made of: each component the scorer weighed,
 *  its score as a bar, and — on hover or focus — the scorer's own sentence
 *  saying how that number came about. The components and their details are
 *  the score-breakdown API's; nothing is recomputed here. */
export default function ComponentBars({ components }: { components: ScoreBreakdownComponent[] }) {
  // A component the scorer couldn't measure is listed with its reason and
  // no bar: the dimension's score was weighed over the others.
  if (!components.some((c) => c.score != null)) return null;
  return (
    <Block label="What the score is made of">
      <ul className="flex flex-col gap-2.5">
        {components.map((c) => {
          if (c.score == null) {
            return (
              <li
                key={c.label}
                className="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_2rem] items-center gap-2.5 text-sm"
              >
                <span className="text-ink-lo">
                  <MetricTooltip text={c.detail}>{c.label}</MetricTooltip>
                </span>
                <span className="text-xs text-ink-lo">not measured</span>
                <span className="text-right font-mono text-ink-lo">n/a</span>
              </li>
            );
          }
          const value = Math.round(c.score);
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
