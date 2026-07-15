"use client";

import { useEffect, useState } from "react";
import { Senator } from "@/types/senator";
import { generateCommentary } from "@/data/commentary";
import { fetchRepHighlights, fetchSenatorHighlights } from "@/lib/api";
import CollapsibleSection from "./CollapsibleSection";

interface DataHighlightsProps {
  senator: Senator;
  chamber?: "senate" | "house";
}

export default function DataHighlights({ senator, chamber = "senate" }: DataHighlightsProps) {
  const staticComments = generateCommentary(senator);
  const [highlights, setHighlights] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setHighlights(null);
    setLoading(true);
    const fetcher = chamber === "house" ? fetchRepHighlights : fetchSenatorHighlights;
    fetcher(senator.id)
      .then((h) => {
        if (h.length > 0) setHighlights(h);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [senator.id, chamber]);

  const comments = highlights ?? staticComments;

  const title = loading ? "DATA HIGHLIGHTS [GENERATING...]" : "DATA HIGHLIGHTS";

  return (
    <CollapsibleSection
      title={title}
      titleColor="text-neon-yellow neon-yellow"
      summary={comments[0]?.slice(0, 80) + (comments[0]?.length > 80 ? "..." : "")}
      source={highlights ? "AI-generated" : undefined}
    >
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
    </CollapsibleSection>
  );
}
