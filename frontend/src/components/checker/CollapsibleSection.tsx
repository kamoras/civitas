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
  titleColor = "text-neon-cyan neon-cyan",
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
          <span className="text-matrix-green/40 text-base font-mono group-hover:text-matrix-green transition-colors" aria-hidden="true">
            {open ? "−" : "+"}
          </span>
          {title}
        </h3>
        <span className="flex items-center gap-3">
          {!open && summary && (
            <span className="text-xs text-matrix-green/50 max-w-xs truncate hidden sm:inline">
              {summary}
            </span>
          )}
          {source && (
            <span className="text-[10px] text-matrix-green/50 hidden sm:inline">
              {source}
            </span>
          )}
        </span>
      </button>
      {alwaysVisible}
      {open && <div id={contentId}>{children}</div>}
    </div>
  );
}
