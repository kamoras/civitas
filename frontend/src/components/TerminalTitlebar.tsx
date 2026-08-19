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
 * is useful, so it becomes a quiet label bar. `title` is rendered verbatim, so
 * pass what the panel HOLDS. Docket-style identifiers (`hr-1234-119`,
 * `ma-ballot`) are the point and belong here; a filename made out of the
 * heading is not. /about, /accessibility and /changelog each used to build
 * `title.toLowerCase().replace(/ /g, "_")` from the very string their <h2>
 * printed underneath, so every panel announced itself twice, once as a file.
 * They carry the heading alone now — if a panel already has a visible heading,
 * it does not need this strip at all.
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
