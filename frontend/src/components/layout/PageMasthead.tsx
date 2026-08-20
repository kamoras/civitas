import React from "react";

/**
 * The standing page header: eyebrow, title, optional standing line, and the
 * 3px section rule that separates a page's identity from its content.
 *
 * Extracted because the same three elements and the same six utility classes
 * were repeated across twelve pages. Repetition is how a design system drifts:
 * the rule weight or the tracking gets adjusted on one page during a fix and
 * nothing else follows, and a year later there are four almost-mastheads.
 *
 * `aside` is for a figure or control that belongs to the page as a whole
 * rather than to any section under it — a partisan-lean readout, a record
 * count. It sits on the baseline of the title on wide screens and wraps
 * underneath on narrow ones.
 */
export default function PageMasthead({
  eyebrow,
  title,
  children,
  aside,
  className = "",
}: {
  eyebrow: string;
  title: React.ReactNode;
  /** Standing line beneath the title. Prose, not a label. */
  children?: React.ReactNode;
  aside?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={`border-b-3 border-ink-min/60 pb-5 ${className}`.trim()}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">{eyebrow}</p>
          <h1 className="mt-3 font-display text-3xl font-extrabold uppercase leading-none tracking-[-0.02em] text-ink-hi sm:text-4xl">
            {title}
          </h1>
        </div>
        {aside && <div className="shrink-0">{aside}</div>}
      </div>
      {children && (
        <div className="mt-3 max-w-2xl font-display text-base leading-relaxed text-ink-lo">
          {children}
        </div>
      )}
    </header>
  );
}
