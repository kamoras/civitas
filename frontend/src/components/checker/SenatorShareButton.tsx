"use client";

import { useState, useEffect, useRef } from "react";

interface SenatorShareButtonProps {
  senatorName: string;
  score: number;
  senatorId: string;
  chamber: string;
}

export default function SenatorShareButton({ senatorName, score, senatorId, chamber }: SenatorShareButtonProps) {
  const [copied, setCopied] = useState(false);
  const [canWebShare, setCanWebShare] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setCanWebShare(typeof navigator !== "undefined" && "share" in navigator);
  }, []);

  const shareUrl = `https://civitas.vote/scorecard?chamber=${chamber}&id=${senatorId}`;
  const shareText = `${senatorName} scores ${score}/100 on the Civitas representation index. See who funds them and how they vote: ${shareUrl} via @civitasvote`;
  const encodedText = encodeURIComponent(shareText);
  const emailSubject = encodeURIComponent(`${senatorName}'s Representation Score — Civitas`);
  const emailBody = encodeURIComponent(`${shareText}\n\nFull scorecard: ${shareUrl}`);

  function handleCopy() {
    navigator.clipboard.writeText(shareUrl).then(() => {
      setCopied(true);
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = setTimeout(() => setCopied(false), 1500);
    });
  }

  async function handleWebShare() {
    try {
      await navigator.share({
        title: `${senatorName} — Civitas Scorecard`,
        text: shareText,
        url: shareUrl,
      });
    } catch {
      // user cancelled
    }
  }

  return (
    <div className="pt-3 border-t border-matrix-green/10 mt-3">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] font-mono tracking-widest text-matrix-green/30 mr-1">SHARE:</span>

        {canWebShare && (
          <button
            onClick={handleWebShare}
            className="text-[10px] font-pixel px-2 py-1 border border-neon-cyan/30 text-neon-cyan/60 hover:text-neon-cyan hover:border-neon-cyan/60 transition-colors"
            aria-label={`Share ${senatorName}'s scorecard via device share menu`}
          >
            [ SHARE ↑ ]
          </button>
        )}

        <a
          href={`https://x.com/intent/tweet?text=${encodedText}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] font-pixel px-2 py-1 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40 transition-colors"
          aria-label={`Share ${senatorName}'s scorecard on X`}
        >
          [ X ]
        </a>

        <a
          href={`https://bsky.app/intent/compose?text=${encodedText}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] font-pixel px-2 py-1 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40 transition-colors"
          aria-label={`Share ${senatorName}'s scorecard on Bluesky`}
        >
          [ BSKY ]
        </a>

        <a
          href={`mailto:?subject=${emailSubject}&body=${emailBody}`}
          className="text-[10px] font-pixel px-2 py-1 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40 transition-colors"
          aria-label={`Share ${senatorName}'s scorecard via email`}
        >
          [ EMAIL ]
        </a>

        <button
          onClick={handleCopy}
          className={`text-[10px] font-pixel px-2 py-1 border transition-colors ${
            copied
              ? "border-neon-cyan/60 text-neon-cyan bg-neon-cyan/10"
              : "border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40"
          }`}
          aria-label={`Copy link to ${senatorName}'s scorecard`}
        >
          {copied ? "[ COPIED! ]" : "[ COPY LINK ]"}
        </button>
      </div>
    </div>
  );
}
