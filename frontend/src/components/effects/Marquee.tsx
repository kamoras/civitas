"use client";

import { usePrefersReducedMotion } from "@/hooks/usePrefersReducedMotion";

export default function Marquee({ items }: { items: string[] }) {
  const text = items.join(" /// ");
  const doubled = `${text} /// ${text} /// `;
  const reducedMotion = usePrefersReducedMotion();

  return (
    <div
      className="w-full overflow-hidden border-y border-matrix-green/10 bg-crt-black/60 py-2.5"
      aria-label="Site information"
    >
      <div className="sr-only">
        <ul>
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </div>
      <div
        aria-hidden="true"
        // inline-block (not the default block width:auto) so this div's own
        // box sizes to its full doubled-text content — translateX(-50%) in
        // the animate-marquee keyframes resolves against that box's own
        // width, and needs it to equal the doubled content's width for the
        // shift to land exactly one copy over (see tailwind.config.ts's
        // marquee keyframe comment).
        className={`inline-block whitespace-nowrap font-mono text-[11px] tracking-widest text-matrix-green/40 ${reducedMotion ? "" : "animate-marquee"}`}
      >
        {reducedMotion ? text : doubled}
      </div>
    </div>
  );
}
