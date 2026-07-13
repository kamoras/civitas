"use client";

import { useState, useRef, useEffect, useId, type ReactNode } from "react";

interface MetricTooltipProps {
  text: string;
  children?: ReactNode;
}

export default function MetricTooltip({ text, children }: MetricTooltipProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const tooltipId = useId();

  useEffect(() => {
    if (!open) return;
    function handleOutside(e: MouseEvent | TouchEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
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
        onClick={() => setOpen(!open)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        className="ml-0.5 text-matrix-green/25 hover:text-matrix-green/60 transition-colors cursor-help text-[9px] leading-none align-super"
        aria-label={`More info: ${text.slice(0, 60)}${text.length > 60 ? "…" : ""}`}
        aria-describedby={open ? tooltipId : undefined}
      >
        [?]
      </button>
      {open && (
        <span
          id={tooltipId}
          role="tooltip"
          className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-48 sm:w-56 px-2.5 py-2 text-[11px] leading-snug text-matrix-green/90 bg-black/95 border border-matrix-green/30 shadow-lg pointer-events-none"
        >
          {text}
        </span>
      )}
    </span>
  );
}
