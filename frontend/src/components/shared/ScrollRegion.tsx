import type { ReactNode } from "react";

/**
 * A table or strip wider than a phone at 200% text, scrolled sideways inside
 * its own box so the page never is (WCAG 1.4.10 Reflow). The box is a named
 * region in the tab order, so a keyboard user can reach it and scroll it
 * with the arrow keys (axe: scrollable-region-focusable), and a screen
 * reader hears what it holds.
 */
export default function ScrollRegion({
  label,
  className = "",
  children,
}: {
  /** What the region holds, read out as its name. */
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div role="region" aria-label={label} tabIndex={0} className={`overflow-x-auto ${className}`}>
      {children}
    </div>
  );
}
