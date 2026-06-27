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
      className="fixed bottom-20 right-4 z-40
                 font-pixel text-[10px] tracking-widest
                 text-neon-cyan/70 hover:text-neon-cyan
                 border border-neon-cyan/30 hover:border-neon-cyan/60
                 bg-crt-black/80 px-3 py-1.5
                 transition-colors backdrop-blur-sm"
    >
      ↑ TOP
    </button>
  );
}
