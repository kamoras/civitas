"use client";

import { Senator } from "@/types/senator";
import { generateCommentary } from "@/data/commentary";

interface PunkCommentaryProps {
  senator: Senator;
}

export default function PunkCommentary({ senator }: PunkCommentaryProps) {
  const comments = generateCommentary(senator);

  return (
    <div>
      <h3
        className="text-lg text-neon-yellow mb-3"
        style={{ textShadow: "0 0 7px #ffff00, 0 0 10px #ffff00" }}
      >
        {">"} DATA HIGHLIGHTS
      </h3>
      <div className="space-y-3">
        {comments.map((comment, i) => (
          <div key={i} className="terminal-window p-4 border-l-2 border-l-neon-yellow/50">
            <p className="text-sm text-matrix-green/80 leading-relaxed">{comment}</p>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-matrix-green/30 mt-3 italic">
        These highlights are auto-generated from the public data above. Verify all figures at the
        sources cited in each section.
      </p>
    </div>
  );
}
