"use client";

import { useEffect, useState } from "react";
import { Senator } from "@/types/senator";
import { generateCommentary } from "@/data/commentary";
import { fetchSenatorHighlights } from "@/lib/api";

interface PunkCommentaryProps {
  senator: Senator;
}

export default function PunkCommentary({ senator }: PunkCommentaryProps) {
  const staticComments = generateCommentary(senator);
  const [highlights, setHighlights] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setHighlights(null);
    setLoading(true);
    fetchSenatorHighlights(senator.id)
      .then((h) => {
        if (h.length > 0) setHighlights(h);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [senator.id]);

  const comments = highlights ?? staticComments;

  return (
    <div>
      <h3
        className="text-lg text-neon-yellow mb-3 flex items-center gap-3"
        style={{ textShadow: "0 0 7px #ffff00, 0 0 10px #ffff00" }}
      >
        {">"} DATA HIGHLIGHTS
        {loading && (
          <span className="text-[10px] text-matrix-green/50 font-normal animate-pulse">
            [GENERATING...]
          </span>
        )}
      </h3>
      <div className="space-y-3">
        {comments.map((comment, i) => (
          <div key={i} className="terminal-window p-4 border-l-2 border-l-neon-yellow/50">
            <p className="text-sm text-matrix-green/80 leading-relaxed">{comment}</p>
          </div>
        ))}
      </div>
      {highlights && (
        <div className="text-[10px] text-matrix-green/25 mt-3">AI-generated · fec.gov · congress.gov</div>
      )}
    </div>
  );
}
