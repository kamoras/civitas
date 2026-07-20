"use client";

import { useState, useRef, useEffect, useId, useCallback, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface MetricTooltipProps {
  text: string;
  children?: ReactNode;
}

// Minimum gap kept between the tooltip and the viewport edge when clamping.
const EDGE_PADDING_PX = 8;
// Gap between the trigger and the tooltip along the vertical axis.
const TRIGGER_GAP_PX = 6;

interface Coords {
  left: number;
  top: number;
}

export default function MetricTooltip({ text, children }: MetricTooltipProps) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<Coords | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const tooltipId = useId();

  // Position the tooltip in viewport coordinates (position: fixed) rather
  // than as an absolutely-positioned child of the trigger. The trigger
  // lives inside `.terminal-window`, which sets `overflow: hidden` — an
  // absolutely-positioned popover near the card's edge is clipped by that
  // ancestor no matter how high its z-index, because overflow clipping and
  // stacking order are independent. Portaling to <body> and positioning
  // with fixed coords escapes every `overflow: hidden`/stacking-context
  // ancestor, so the tooltip always draws over everything.
  const reposition = useCallback(() => {
    if (!ref.current || !tooltipRef.current) return;
    const triggerRect = ref.current.getBoundingClientRect();
    const tipWidth = tooltipRef.current.offsetWidth;
    const tipHeight = tooltipRef.current.offsetHeight;

    // Horizontal: center on the trigger, then clamp both edges to the
    // viewport so the text is never cut off near a screen edge.
    const center = triggerRect.left + triggerRect.width / 2;
    let left = center - tipWidth / 2;
    const maxLeft = window.innerWidth - EDGE_PADDING_PX - tipWidth;
    left = Math.min(Math.max(left, EDGE_PADDING_PX), Math.max(EDGE_PADDING_PX, maxLeft));

    // Vertical: prefer above the trigger; flip below when there isn't room
    // (e.g. a metric near the very top of the card / viewport).
    let top = triggerRect.top - tipHeight - TRIGGER_GAP_PX;
    if (triggerRect.top < tipHeight + TRIGGER_GAP_PX + EDGE_PADDING_PX) {
      top = triggerRect.bottom + TRIGGER_GAP_PX;
    }

    setCoords({ left, top });
  }, []);

  // Measure + place once open. The bubble renders at opacity-0 / off-screen
  // until coords are set, so there's no flash at the wrong spot even though
  // this runs after the first paint (kept as useEffect, not layout effect,
  // to avoid the "useLayoutEffect does nothing on the server" SSR warning).
  useEffect(() => {
    if (!open) return;
    reposition();
  }, [open, reposition]);

  // Keep it anchored while open: fixed coordinates are viewport-relative, so
  // scrolling or resizing the page would otherwise leave the bubble stranded.
  useEffect(() => {
    if (!open) return;
    const onScroll = () => reposition();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    function handleOutside(e: MouseEvent | TouchEvent) {
      const target = e.target as Node;
      if (ref.current?.contains(target)) return;
      if (tooltipRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("touchstart", handleOutside);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("touchstart", handleOutside);
    };
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex items-center">
      {children}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        title={text}
        className="ml-0.5 text-matrix-green/25 hover:text-matrix-green/60 transition-colors cursor-help text-[9px] leading-none align-super"
        aria-label={`More info: ${text.slice(0, 60)}${text.length > 60 ? "…" : ""}`}
        aria-describedby={tooltipId}
      >
        [?]
      </button>
      {/*
        Screen-reader anchor for aria-describedby — always in the DOM so the
        description resolves regardless of hover/click/portal state. The
        visible bubble below is portaled and aria-hidden (decorative), so
        assistive tech reads this text, not the floating copy.
      */}
      <span id={tooltipId} className="sr-only">{text}</span>
      {open && createPortal(
        <div
          ref={tooltipRef}
          role="tooltip"
          aria-hidden="true"
          style={{
            left: coords ? `${coords.left}px` : "-9999px",
            top: coords ? `${coords.top}px` : "-9999px",
          }}
          className={`fixed z-[100] w-48 sm:w-56 max-w-[calc(100vw-16px)] px-2.5 py-2 text-[11px] leading-snug text-matrix-green/90 bg-black/95 border border-matrix-green/30 shadow-lg pointer-events-none transition-opacity ${
            coords ? "opacity-100" : "opacity-0"
          }`}
        >
          {text}
        </div>,
        document.body,
      )}
    </span>
  );
}
