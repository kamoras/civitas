"use client";

import { useEffect, useRef, useState } from "react";
import { scrollBehavior } from "@/lib/motion";

// What the floating button must never sit on: anything a reader can press,
// type in or focus, and charts and maps (a split town's map is tapped).
const INTERACTIVE =
  "a,button,input,select,textarea,summary,label,svg,[role=button],[role=tab],[tabindex]";

/** True when an interactive element is under any corner or the middle of `r`. */
export function coversInteractive(r: DOMRect, self: Element): boolean {
  const points: [number, number][] = [
    [r.left + 1, r.top + 1],
    [r.right - 1, r.top + 1],
    [r.left + 1, r.bottom - 1],
    [r.right - 1, r.bottom - 1],
    [(r.left + r.right) / 2, (r.top + r.bottom) / 2],
  ];
  return points.some(([x, y]) =>
    (document.elementsFromPoint?.(x, y) ?? []).some(
      (e) => !self.contains(e) && e.closest(INTERACTIVE) !== null
    )
  );
}

/**
 * A floating "back to top" that steps aside: it shows only once the page
 * has scrolled, and hides while a link, control, map or chart is under it,
 * so it never covers something the reader means to press or a focused
 * element (WCAG 2.4.11).
 */
export default function BackToTop() {
  const ref = useRef<HTMLButtonElement>(null);
  const [scrolled, setScrolled] = useState(false);
  const [covering, setCovering] = useState(false);

  useEffect(() => {
    let frame = 0;
    const check = () => {
      frame = 0;
      setScrolled(window.scrollY > 500);
      const el = ref.current;
      if (el) setCovering(coversInteractive(el.getBoundingClientRect(), el));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(check);
    };
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    document.addEventListener("focusin", schedule);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      document.removeEventListener("focusin", schedule);
    };
  }, []);

  // Rendered invisible (out of the tab order and the accessibility tree)
  // until shown, so its place can be measured.
  const shown = scrolled && !covering;
  return (
    <button
      ref={ref}
      onClick={() => window.scrollTo({ top: 0, behavior: scrollBehavior() })}
      aria-label="Back to top"
      className={`fixed bottom-20 right-4 z-40 font-mono text-xs
                 text-signal-cyan hover:text-phos
                 border border-white/15 hover:border-signal-cyan/40
                 bg-surface-base px-3 py-1.5 min-h-6
                 transition-colors backdrop-blur-sm ${shown ? "" : "invisible"}`}
    >
      ↑ Top
    </button>
  );
}
