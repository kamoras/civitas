import type { ReactNode } from "react";
import { displayScore } from "@/lib/formatting";
import { SHARE_EXCLUDE_ATTR, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import MetricTooltip from "@/components/checker/MetricTooltip";
import { onThisPage } from "@/lib/onThisPage";
import { TYPE } from "@/lib/typeStyles";

/** One column of a record: a member's money, votes or bills, or a
 *  president's scored dimension (with its score), and the evidence behind
 *  it, laid out the way the ballot page lays out a contest (a
 *  shaded header over a hairline-ruled box). The body grows so columns in
 *  a row share one height, and the "all of it" link sits on the bottom
 *  edge, level across the row. */
export default function ScoreColumn({
  title,
  shareId,
  tooltip,
  score,
  finding,
  method,
  more,
  aside,
  children,
}: {
  title: string;
  /** What the column's score measures, in plain words ("What this means" beside the title). */
  tooltip?: string;
  /** The column's anchor id; also what its shared image and link are named by. */
  shareId: string;
  score?: number | null;
  /** What the record shows, in one plain sentence (the breakdown API's). */
  finding?: string | null;
  /** How the score is computed (its components and their statistics),
   *  kept behind a disclosure under the evidence. */
  method?: ReactNode;
  more?: { label: string; onClick: () => void };
  /** In place of the score, for a column on record but not scored. */
  aside?: string;
  children: ReactNode;
}) {
  const shown = score == null ? null : displayScore(score);
  return (
    <section
      id={shareId}
      {...{ [SHARE_SECTION_ATTR]: shareId }}
      {...onThisPage(title, shown ?? undefined)}
      className="flex flex-col border border-white/25 bg-surface font-sans"
    >
      <header className="flex items-end justify-between gap-3 border-b border-white/25 bg-surface-raised px-5 py-4">
        <div className="min-w-0">
          <h2 className={TYPE.section}>
            {tooltip ? <MetricTooltip text={tooltip}>{title}</MetricTooltip> : title}
          </h2>
        </div>
        {aside ? <span className="shrink-0 font-mono text-small text-ink-min">{aside}</span> : null}
      </header>
      <div className="flex flex-1 flex-col gap-5 px-5 py-4">
        {finding && <p className={TYPE.boxTitle}>{finding}</p>}
        {children}
        {(method || shown != null) && (
          <details
            {...{ [SHARE_EXCLUDE_ATTR]: "" }}
            className="group border-t border-white/[0.08] pt-4"
          >
            <summary
              className={`flex min-h-[44px] cursor-pointer list-none items-center gap-2 text-small text-ink-lo hover:text-phos [&::-webkit-details-marker]:hidden`}
            >
              <span aria-hidden="true" className="inline-block group-open:rotate-90">
                &#9656;
              </span>
              How this is computed
            </summary>
            <div className="flex flex-col gap-4 pt-2">
              {shown != null && (
                <p className="text-small text-ink-lo">
                  The scorer&apos;s arithmetic puts this at {shown} out of 100.
                </p>
              )}
              {method}
            </div>
          </details>
        )}
        <div className="mt-auto flex flex-wrap items-center justify-between gap-3">
          {more && (
            <button
              type="button"
              onClick={more.onClick}
              {...{ [SHARE_EXCLUDE_ATTR]: "" }}
              className="min-h-[44px] text-left font-mono text-small text-ink-lo underline underline-offset-2 hover:text-phos"
            >
              {more.label} &rarr;
            </button>
          )}
          <span className="ml-auto">
            <ShareSectionButton label={title} />
          </span>
        </div>
      </div>
    </section>
  );
}

/** A short label over a block inside a column, in the kicker style. */
export function BlockLabel({ children }: { children: ReactNode }) {
  return <h3 className={TYPE.kicker}>{children}</h3>;
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
