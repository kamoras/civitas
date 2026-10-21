import type { ReactNode } from "react";
import { BoxAnchor } from "./RecordBox";

const GRID: Record<1 | 2 | 3, string> = {
  1: "",
  2: "@min-[40rem]:grid-cols-2",
  3: "@min-[40rem]:grid-cols-2 @min-[62rem]:grid-cols-3",
};

/**
 * RecordBoxes in ruled columns, the front page's newspaper rules: a 2px
 * ink rule over the grid, a hairline between columns and under each box.
 * One column under 40rem of the grid's own width (not the window's, so a
 * grid in a side column or at 200% text reflows too), two from 40rem,
 * three from 62rem when `columns` is 3.
 *
 * Each box draws the rule on its own left; this clips the ones left of the
 * first column. The clip is a little wider than the grid so a focus ring
 * on the first column is never cut.
 */
export default function BoxGrid({
  columns = 3,
  label,
  labelId,
  labelLevel = 2,
  labelHref,
  labelHrefText,
  children,
  className = "",
}: {
  columns?: 1 | 2 | 3;
  /** The grid's heading: what the boxes are. */
  label?: string;
  labelId?: string;
  /** The heading's level: an h2, or an h3 for a grid under a page section's
   *  own h2 (give its boxes `level={4}`). */
  labelLevel?: 2 | 3;
  /** A link beside the heading to the whole list the boxes come from. */
  labelHref?: string;
  labelHrefText?: string;
  children: ReactNode;
  className?: string;
}) {
  const Heading = `h${labelLevel}` as const;
  return (
    <section
      aria-labelledby={label ? labelId : undefined}
      className={`@container -mx-2 overflow-x-clip px-2 ${className}`}
    >
      {label && (
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b-2 border-ink-hi pb-1.5">
          <Heading id={labelId} className="font-display text-xl font-semibold text-ink-hi">
            {label}
          </Heading>
          {labelHref && labelHrefText && (
            <BoxAnchor
              href={labelHref}
              className="inline-flex min-h-6 items-center text-sm text-signal-cyan underline underline-offset-2 hover:text-phos"
            >
              {labelHrefText}
            </BoxAnchor>
          )}
        </div>
      )}
      <div
        className={`grid grid-cols-[minmax(0,1fr)] gap-x-10 ${GRID[columns]} ${
          label ? "" : "border-t-2 border-ink-hi"
        }`}
      >
        {children}
      </div>
    </section>
  );
}
