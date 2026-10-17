"use client";

import { useEffect } from "react";

/** The fixed header's bottom edge plus this much air, in px. */
const AIR_PX = 8;

/**
 * Keeps every page's top clearance (`--header-clearance`, globals.css)
 * below the fixed header when the header is taller than the 6rem the
 * stylesheet reserves. That happens only when its bar wraps: on a phone at
 * a large text size, where the wordmark and the SEARCH and MENU buttons
 * can't share one row (WCAG 1.4.10). At any ordinary size the header fits
 * and this leaves the stylesheet's value alone, so nothing moves.
 *
 * Measured, not a second fixed number: a wrapped header's height depends on
 * the text size and the buttons beside the wordmark (the header search is
 * one), and a hard-coded value would drift from them.
 */
export default function HeaderClearance() {
  useEffect(() => {
    const header = document.querySelector<HTMLElement>('header[role="banner"]');
    if (!header || typeof ResizeObserver === "undefined") return;
    const root = document.documentElement;
    const update = () => {
      root.style.removeProperty("--header-clearance");
      const reserved =
        parseFloat(getComputedStyle(root).getPropertyValue("--header-clearance")) *
        parseFloat(getComputedStyle(root).fontSize);
      const needed = header.getBoundingClientRect().height + AIR_PX;
      if (needed > reserved) root.style.setProperty("--header-clearance", `${Math.ceil(needed)}px`);
    };
    const observer = new ResizeObserver(update);
    observer.observe(header);
    update();
    return () => {
      observer.disconnect();
      root.style.removeProperty("--header-clearance");
    };
  }, []);
  return null;
}
