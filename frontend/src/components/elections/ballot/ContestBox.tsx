import type { ReactNode } from "react";

/** One contest, set the way a printed ballot sets it: a shaded header
 * carrying the office and the ballot's own instruction ("Vote for one"),
 * then its rows. Left-aligned, sentence case, the name bolder than the
 * party — the Center for Civic Design / EAC ballot conventions, in the
 * site's dark ink rather than on white paper. */
export default function ContestBox({
  title,
  subtitle,
  instruction,
  headingLevel = 3,
  children,
}: {
  title: string;
  subtitle?: string;
  instruction?: string | null;
  headingLevel?: 2 | 3;
  children?: ReactNode;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className="border border-white/25 bg-surface font-sans">
      <header className="flex items-baseline justify-between gap-3 border-b border-white/25 bg-surface-raised px-4 py-3">
        <div className="min-w-0">
          <Heading className="text-[17px] font-bold leading-tight text-ink-hi">{title}</Heading>
          {subtitle && <p className="mt-0.5 text-[13px] text-ink-lo">{subtitle}</p>}
        </div>
        {instruction && (
          <span className="shrink-0 font-mono text-xs text-ink-lo">{instruction}</span>
        )}
      </header>
      {children}
    </section>
  );
}
