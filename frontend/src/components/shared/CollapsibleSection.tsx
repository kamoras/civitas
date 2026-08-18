"use client";

import { useId, useState, type ReactNode } from "react";

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

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-baseline justify-between mb-3 group cursor-pointer"
        aria-expanded={open}
        aria-controls={contentId}
      >
        <h3 className={`text-lg ${titleColor} flex items-center gap-2`}>
          <span
            className="text-ink-min text-base font-mono group-hover:text-phos transition-colors"
            aria-hidden="true"
          >
            {open ? "−" : "+"}
          </span>
          {title}
        </h3>
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
