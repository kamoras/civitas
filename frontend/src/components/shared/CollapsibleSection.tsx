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

/** Controlled mode takes both props or neither — `open` alone would leave the
 * header toggling state nobody reads. */
type OpenControl =
  | { open: boolean; onOpenChange: (open: boolean) => void }
  | { open?: undefined; onOpenChange?: undefined };

type CollapsibleSectionProps = OpenControl & {
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
  /** False when there is nothing to expand onto: the header renders as a
   * plain heading, with no toggle, and only `alwaysVisible` shows. */
  expandable?: boolean;
  children?: ReactNode;
};

export default function CollapsibleSection({
  title,
  titleColor = "text-signal-cyan",
  summary,
  alwaysVisible,
  defaultOpen = false,
  source,
  open: controlledOpen,
  onOpenChange,
  expandable = true,
  children,
}: CollapsibleSectionProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = onOpenChange ?? setUncontrolledOpen;
  const contentId = useId();
  const titleId = useId();
  const Heading = useContext(SectionHeadingLevel);

  if (!expandable) {
    return (
      <div>
        <div className="w-full flex items-baseline justify-between mb-3">
          <Heading className={`text-lg ${titleColor}`}>{title}</Heading>
          <span className="flex items-center gap-3">
            {summary && <span className="text-xs text-ink-lo max-w-xs truncate hidden sm:inline">{summary}</span>}
            {source && <span className="text-xs text-ink-lo hidden sm:inline">{source}</span>}
          </span>
        </div>
        {alwaysVisible}
      </div>
    );
  }

  // The toggle sits inside the heading, not the heading inside the toggle:
  // a button's content model is phrasing-only, and a heading wrapped in one
  // drops out of the screen-reader heading list. The heading spans the row
  // and the button fills it — title, summary and source together — so the
  // whole header is one click target, as it always was, and the focus ring
  // outlines exactly that target. The heading is named by its title alone
  // (aria-labelledby): the summary beside it is a changing status line, and
  // a heading list should read the same whether the section is open or not.
  return (
    <div>
      <Heading className={`text-lg ${titleColor} mb-3`} aria-labelledby={titleId}>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="group w-full flex items-baseline justify-between gap-3 cursor-pointer text-left"
          aria-expanded={open}
          aria-controls={contentId}
        >
          <span className="flex items-center gap-2 min-w-0">
            <span
              className="text-ink-min text-base font-mono group-hover:text-phos transition-colors"
              aria-hidden="true"
            >
              {open ? "−" : "+"}
            </span>
            <span id={titleId}>{title}</span>
          </span>
          <span className="flex items-center gap-3 font-normal normal-case tracking-normal">
            {!open && summary && (
              <span className="text-xs text-ink-lo max-w-xs truncate hidden sm:inline">
                {summary}
              </span>
            )}
            {source && <span className="text-xs text-ink-lo hidden sm:inline">{source}</span>}
          </span>
        </button>
      </Heading>
      {alwaysVisible}
      {/* Always in the DOM so aria-controls resolves; the body mounts only
          when open, so collapsed sections fetch nothing. */}
      <div id={contentId} hidden={!open}>
        {open && children}
      </div>
    </div>
  );
}
