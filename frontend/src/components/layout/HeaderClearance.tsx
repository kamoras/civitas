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
 *
 * Also publishes the header's own height as `--header-height`, for anything that
 * sits directly under it (the phone's "on this page" bar, the Action Center's tabs): 86px today, more
 * when the bar wraps.
 */
export default function HeaderClearance() {
  useEffect(() => {
    const header = document.querySelector<HTMLElement>('header[role="banner"]');
    if (!header || typeof ResizeObserver === "undefined") return;
    const root = document.documentElement;
    const update = () => {
      // The header's own bottom, for what sticks under it (the Action
      // Center's tabs, the phone's section bar), at any size.
      root.style.setProperty(
        "--header-height",
        `${Math.ceil(header.getBoundingClientRect().height)}px`
      );
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
      root.style.removeProperty("--header-height");
    };
  }, []);
  return null;
}
