"use client";

import { useEffect, useState } from "react";
import { Senator } from "@/types/senator";
import { generateCommentary } from "@/data/commentary";
import { fetchRepHighlights, fetchSenatorHighlights } from "@/lib/api";
import CollapsibleSection from "../shared/CollapsibleSection";

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

  // Both `highlights` (fetched from /highlights, see backend's
  // build_highlights) and `staticComments` (generateCommentary, this
  // file's client-side fallback) are template-filled facts from FEC/
  // Congress.gov data — neither is LLM output (both backend routes'
  // own docstrings say "no LLM, pure data"). The "source" caption below
  // used to say "AI-generated" for the fetched path, which was simply
  // wrong — corrected to say what actually happens (2026-08 review).
  const comments = highlights ?? staticComments;

  const title = loading ? "DATA HIGHLIGHTS [GENERATING...]" : "DATA HIGHLIGHTS";

  return (
    <CollapsibleSection
      title={title}
      titleColor="text-signal-amber"
      summary={comments[0]?.slice(0, 80) + (comments[0]?.length > 80 ? "..." : "")}
      source={highlights ? "Auto-generated from data" : undefined}
    >
      <div className="space-y-3">
        {comments.map((comment, i) => (
          <div key={i} className="panel p-4 border-l-2 border-l-signal-amber">
            <p className="text-base text-ink leading-relaxed">{comment}</p>
          </div>
        ))}
      </div>
      {highlights && (
        <div className="text-xs text-ink-min mt-3">
          Auto-generated from data (no LLM) · fec.gov · congress.gov
        </div>
      )}
    </CollapsibleSection>
  );
}
