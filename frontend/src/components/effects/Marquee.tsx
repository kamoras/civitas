"use client";

import { useEffect, useState } from "react";

export default function Marquee({ items }: { items: string[] }) {
  const text = items.join(" /// ");
  const doubled = `${text} /// ${text} /// `;
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return (
    <div
      className="w-full overflow-hidden border-y border-matrix-green/30 bg-crt-black/80 py-3"
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
        className={`whitespace-nowrap font-terminal text-lg text-matrix-green/70 ${reducedMotion ? "" : "animate-marquee"}`}
      >
        {reducedMotion ? text : doubled}
      </div>
    </div>
  );
}
