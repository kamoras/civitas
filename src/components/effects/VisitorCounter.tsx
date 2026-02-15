"use client";

import { useState, useEffect } from "react";

export default function VisitorCounter() {
  const [count, setCount] = useState("000000");

  useEffect(() => {
    let stored = localStorage.getItem("mp-visitor-count");
    if (!stored) {
      // Start with a random base number for authenticity
      const base = 34892 + Math.floor(Math.random() * 1000);
      stored = String(base);
      localStorage.setItem("mp-visitor-count", stored);
    } else {
      // Increment on each visit
      stored = String(parseInt(stored) + 1);
      localStorage.setItem("mp-visitor-count", stored);
    }
    setCount(stored.padStart(8, "0"));
  }, []);

  return (
    <div className="inline-flex items-center gap-2 font-pixel text-[10px] text-matrix-green/60">
      <span>YOU ARE VISITOR #</span>
      <span className="bg-black border border-matrix-green/30 px-2 py-1 tracking-widest">
        {count}
      </span>
    </div>
  );
}
