"use client";

import { usePrefersReducedMotion } from "@/hooks/usePrefersReducedMotion";

export default function Marquee({ items }: { items: string[] }) {
  const text = items.join(" /// ");
  const doubled = `${text} /// ${text} /// `;
  const reducedMotion = usePrefersReducedMotion();

  return (
    <div
      className="group w-full overflow-hidden border-y border-white/[0.07] bg-surface-base py-2.5"
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
        //
        // group-hover pauses it: this text is real first-visit content (data
        // sources, the no-tracking privacy stance), not decorative filler,
        // and at 30s/loop and 11px there was previously no way for a mouse
        // user to stop it and actually read a given item (2026-08 review).
        className={`inline-block whitespace-nowrap font-mono text-xs tracking-widest text-ink-min ${reducedMotion ? "" : "animate-marquee group-hover:[animation-play-state:paused]"}`}
      >
        {reducedMotion ? text : doubled}
      </div>
    </div>
  );
}
