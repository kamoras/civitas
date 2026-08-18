"use client";

import { useEffect, useState } from "react";

export default function BackToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => {
      setVisible(window.scrollY > 500);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!visible) return null;

  return (
    <button
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      aria-label="Back to top"
      className="fixed bottom-20 right-4 z-40 font-mono text-xs tracking-widest
                 text-signal-cyan hover:text-phos
                 border border-white/15 hover:border-signal-cyan/40
                 bg-surface-base px-3 py-1.5
                 transition-colors backdrop-blur-sm"
    >
      ↑ TOP
    </button>
  );
}
