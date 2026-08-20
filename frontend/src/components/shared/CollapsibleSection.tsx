"use client";

import { createContext, useContext, useId, useState, type ReactNode } from "react";

/**
 * What heading level these sections sit at.
 *
 * The level is a property of where the section is mounted, not of the section,
 * so it is read from context rather than passed down: the five components that
 * render one of these (VotingRecord, SponsoredBills, StockTrades,
 * PlatformTracker, DataHighlights) are all mounted by SenatorCard, and
 * threading a prop through each of them to say "you are one level down from my
 * title" is more code than reading it.
 *
 * Defaults to h3, which is what every one of them rendered before the member's
 * name became the page's h1 on /politicians/[id] — after which h1 → h3 skipped
 * a level and axe-core reported `heading-order` on every profile.
 */
const SectionHeadingLevel = createContext<"h2" | "h3">("h3");

export const SectionHeadingLevelProvider = SectionHeadingLevel.Provider;

interface CollapsibleSectionProps {
  title: string;
  titleColor?: string;
  /** Compact summary shown on the right side of the header when collapsed */
  summary?: ReactNode;
  /** Content always shown above the collapsible body (e.g., stat boxes) */
  alwaysVisible?: ReactNode;
  /** Whether the section starts expanded */
  defaultOpen?: boolean;
  /** Source attribution text */
  source?: string;
  children: ReactNode;
}

export default function CollapsibleSection({
  title,
  titleColor = "text-signal-cyan",
  summary,
  alwaysVisible,
  defaultOpen = false,
  source,
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = useId();
  const Heading = useContext(SectionHeadingLevel);

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-baseline justify-between mb-3 group cursor-pointer"
        aria-expanded={open}
        aria-controls={contentId}
      >
        <Heading className={`text-lg ${titleColor} flex items-center gap-2`}>
          <span
            className="text-ink-min text-base font-mono group-hover:text-phos transition-colors"
            aria-hidden="true"
          >
            {open ? "−" : "+"}
          </span>
          {title}
        </Heading>
        <span className="flex items-center gap-3">
          {!open && summary && (
            <span className="text-xs text-ink-lo max-w-xs truncate hidden sm:inline">
              {summary}
            </span>
          )}
          {source && <span className="text-xs text-ink-lo hidden sm:inline">{source}</span>}
        </span>
      </button>
      {alwaysVisible}
      {open && <div id={contentId}>{children}</div>}
    </div>
  );
}
