import type { ReactNode } from "react";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";

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
  shareId,
  shareAnchor,
  children,
}: {
  title: string;
  /** When set, the box offers itself as a shared image, named by this id,
   *  linking to the page with `shareAnchor` (the fragment that opens this
   *  contest; see `contestHash`). */
  shareId?: string;
  shareAnchor?: string;
  subtitle?: string;
  instruction?: string | null;
  headingLevel?: 2 | 3;
  children?: ReactNode;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      className="border border-white/25 bg-surface font-sans"
      {...(shareId ? { [SHARE_SECTION_ATTR]: shareId } : {})}
    >
      {/* Wraps: in a desktop ballot column the instruction and the Share
          button together would otherwise squeeze the title to a word a line. */}
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-2 border-b border-white/25 bg-surface-raised px-4 py-3">
        <div className="min-w-0">
          <Heading className="text-[17px] font-bold leading-tight text-ink-hi">{title}</Heading>
          {subtitle && <p className="mt-0.5 text-[13px] text-ink-lo">{subtitle}</p>}
        </div>
        {(instruction || shareId) && (
          <span className="flex shrink-0 items-center gap-3">
            {instruction && <span className="font-mono text-xs text-ink-lo">{instruction}</span>}
            {shareId && <ShareSectionButton label={title} anchor={shareAnchor ?? null} />}
          </span>
        )}
      </header>
      {children}
    </section>
  );
}
