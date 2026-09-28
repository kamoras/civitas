import type { ReactNode } from "react";
import { displayScore } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";

/** One of a member's three scored dimensions, as a column: its name, its
 *  share of the Representation Score, the score itself, and the evidence
 *  behind it — laid out the way the ballot page lays out a contest (a
 *  shaded header over a hairline-ruled box). The body grows so columns in
 *  a row share one height, and the "all of it" link sits on the bottom
 *  edge, level across the row. */
export default function ScoreColumn({
  title,
  weight,
  score,
  more,
  aside,
  children,
}: {
  title: string;
  /** Share of the overall score, 0–1; omitted when the config hasn't loaded. */
  weight?: number;
  score: number | null | undefined;
  more?: { label: string; onClick: () => void };
  /** In place of the score, for a column on record but not scored. */
  aside?: string;
  children: ReactNode;
}) {
  const shown = score == null ? null : displayScore(score);
  return (
    <section className="flex flex-col border border-white/25 bg-surface font-sans">
      <header className="flex items-end justify-between gap-3 border-b border-white/25 bg-surface-raised px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-[19px] font-bold leading-tight text-ink-hi">{title}</h2>
          {weight != null && (
            <p className="mt-1 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
              {Math.round(weight * 100)}% of the score
            </p>
          )}
        </div>
        {aside ? (
          <span className="shrink-0 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
            {aside}
          </span>
        ) : (
          <span
            className={`shrink-0 font-display text-[44px] font-extrabold leading-none ${
              shown == null ? "text-ink-min" : getScoreColor(shown)
            }`}
            aria-label={shown == null ? "Not scored" : `${shown} out of 100`}
          >
            {shown ?? "—"}
          </span>
        )}
      </header>
      <div className="flex flex-1 flex-col gap-5 px-5 py-4">
        {children}
        {more && (
          <button
            type="button"
            onClick={more.onClick}
            className="mt-auto min-h-[44px] self-start text-left font-mono text-[13px] text-ink-lo underline underline-offset-2 hover:text-phos"
          >
            {more.label} &rarr;
          </button>
        )}
      </div>
    </section>
  );
}

/** A small uppercase label over a block inside a column. */
export function BlockLabel({ children }: { children: ReactNode }) {
  return <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">{children}</h3>;
}

/** A column block set off by a hairline above it. */
export function Block({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-t border-white/[0.08] pt-4 first:border-t-0 first:pt-0">
      <BlockLabel>{label}</BlockLabel>
      {children}
    </div>
  );
}
