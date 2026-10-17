import React from "react";
import { labelCase } from "@/lib/controlStyles";

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
 * underneath on narrow ones — never wider than the page: `shrink-0` keeps a
 * short aside from being squeezed beside the title, and `max-w-full` caps a
 * long one (the /elections status line, "REFRESH FAILED · …") at the
 * header's width so its text wraps. Without the cap the aside took its
 * one-line width and the whole page scrolled sideways on a phone.
 *
 * `more` continues the standing line (or is all of it, with no children).
 * From 640px up it reads straight on after it; on a phone it waits behind
 * "More about this page", so the first screen is the page (mobile spec P3).
 * Inline content (text), so the wide layout reads as one paragraph.
 */
export default function PageMasthead({
  eyebrow,
  title,
  children,
  aside,
  more,
  className = "",
}: {
  eyebrow: string;
  title: React.ReactNode;
  /** Standing line beneath the title. Prose, not a label. */
  children?: React.ReactNode;
  aside?: React.ReactNode;
  /** The rest of the standing line, folded on a phone. */
  more?: React.ReactNode;
  className?: string;
}) {
  return (
    <header className={`border-b-3 border-ink-min/60 pb-5 ${className}`.trim()}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className={`font-mono tracking-[0.16em] text-ink-min ${labelCase(eyebrow)}`}>
            {eyebrow}
          </p>
          {/* Caps from 640px up; a phone's title is set as written, in
              sentence case, where caps at 30px wrapped "TODAY ON THE
              RECORD" to two lines (mobile spec P1). */}
          <h1 className="mt-3 font-display text-3xl font-extrabold leading-[1.1] tracking-[-0.02em] text-ink-hi sm:text-4xl sm:uppercase sm:leading-none">
            {title}
          </h1>
        </div>
        {aside && <div className="min-w-0 max-w-full shrink-0">{aside}</div>}
      </div>
      {(children || more) && (
        <div
          className={`mt-3 max-w-2xl font-display text-base leading-relaxed text-ink-lo ${children ? "" : "hidden sm:block"}`}
        >
          {children}
          {more && (
            <span className="hidden sm:inline">
              {children ? " " : ""}
              {more}
            </span>
          )}
        </div>
      )}
      {more && (
        <details className="mt-1 sm:hidden">
          <summary className="inline-flex min-h-11 cursor-pointer items-center font-mono text-[0.8125rem] uppercase tracking-[0.08em] text-ink-lo hover:text-phos">
            More about this page
          </summary>
          <p className="max-w-2xl font-display text-base leading-relaxed text-ink-lo">{more}</p>
        </details>
      )}
    </header>
  );
}
