import React from "react";

/**
 * A panel's label strip.
 *
 * Was a fake macOS window chrome — three traffic-light dots and a filename
 * like `senate_leaderboard.db`. Stacked four-deep on a page it was the
 * loudest thing on screen, and it dressed people and votes up as files on a
 * disk, which is the opposite of what a public record is.
 *
 * Kept as a component rather than deleted at ~85 call sites: the strip itself
 * is useful, so it becomes a quiet label bar. Callers still pass filename-ish
 * titles in places; `title` is rendered verbatim and cleaning those up is a
 * per-page job, not this component's.
 *
 * Still aria-hidden. The strip labels a panel that carries its own heading in
 * the accessibility tree; announcing both would just double up.
 */
export default function TerminalTitlebar({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className="flex items-center justify-between gap-3 border-b border-white/[0.07] bg-surface-raised px-4 py-2"
      aria-hidden="true"
    >
      <span className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">{title}</span>
      {children}
    </div>
  );
}
