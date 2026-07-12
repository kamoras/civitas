"use client";

import { useState, useRef, type KeyboardEvent } from "react";
import type { ActionIssue } from "@/types/action";

interface ShareButtonsProps {
  issue: ActionIssue;
  className?: string;
}

function buildShareText(title: string, shareUrl: string): string {
  const full = `${title} — Track this issue and your reps' stances: ${shareUrl} via @civitasvote #CivicTransparency`;
  if (full.length <= 240) return full;

  // Try without hashtag first
  const noHashtag = `${title} — Track this issue and your reps' stances: ${shareUrl} via @civitasvote`;
  if (noHashtag.length <= 240) return noHashtag;

  // Try without handle either
  const noHandle = `${title} — Track this issue and your reps' stances: ${shareUrl}`;
  if (noHandle.length <= 240) return noHandle;

  // Hard trim as last resort
  return noHandle.slice(0, 237) + "...";
}

export default function ShareButtons({ issue, className = "" }: ShareButtonsProps) {
  const shareUrl = `https://civitas-research.org/action?issue=${issue.id}`;
  const shareText = buildShareText(issue.title, shareUrl);
  const encodedText = encodeURIComponent(shareText);

  const [copied, setCopied] = useState(false);
  const [mastodonInstance, setMastodonInstance] = useState("mastodon.social");
  const [showMastodonInput, setShowMastodonInput] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function handleCopy() {
    navigator.clipboard.writeText(shareUrl).then(() => {
      setCopied(true);
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = setTimeout(() => setCopied(false), 1500);
    });
  }

  function handleMastodonShare() {
    const instance = mastodonInstance.trim().replace(/^https?:\/\//, "");
    if (!instance) return;
    const url = `https://${instance}/share?text=${encodedText}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  return (
    <div className={`pt-4 border-t border-matrix-green/10 ${className}`}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] font-pixel text-matrix-green/30 mr-1">SHARE:</span>

        {/* X / Twitter */}
        <a
          href={`https://x.com/intent/tweet?text=${encodedText}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] font-pixel px-2 py-1 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40 transition-colors bg-transparent"
          aria-label="Share on X (Twitter)"
        >
          [ X ]
        </a>

        {/* Bluesky */}
        <a
          href={`https://bsky.app/intent/compose?text=${encodedText}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] font-pixel px-2 py-1 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40 transition-colors bg-transparent"
          aria-label="Share on Bluesky"
        >
          [ BSKY ]
        </a>

        {/* Mastodon — toggle inline form */}
        {!showMastodonInput ? (
          <button
            onClick={() => setShowMastodonInput(true)}
            className="text-[10px] font-pixel px-2 py-1 border border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40 transition-colors"
            aria-label="Share on Mastodon"
          >
            [ MASTODON ]
          </button>
        ) : (
          <span className="flex items-center gap-1">
            <input
              type="text"
              value={mastodonInstance}
              onChange={(e) => setMastodonInstance(e.target.value)}
              onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
                if (e.key === "Enter") handleMastodonShare();
                if (e.key === "Escape") setShowMastodonInput(false);
              }}
              placeholder="mastodon.social"
              aria-label="Mastodon instance"
              className="text-[10px] font-mono bg-crt-black border border-neon-cyan/30 text-neon-cyan px-2 py-1 w-32 focus:outline-none focus:border-neon-cyan/60"
              // eslint-disable-next-line jsx-a11y/no-autofocus
              autoFocus
            />
            <button
              onClick={handleMastodonShare}
              className="text-[10px] font-pixel px-2 py-1 border border-neon-cyan/30 text-neon-cyan/70 hover:text-neon-cyan hover:border-neon-cyan/60 transition-colors"
              aria-label="Open Mastodon share"
            >
              GO
            </button>
            <button
              onClick={() => setShowMastodonInput(false)}
              className="text-[10px] font-pixel text-matrix-green/30 hover:text-matrix-green/60 transition-colors px-1"
              aria-label="Cancel Mastodon share"
            >
              ✕
            </button>
          </span>
        )}

        {/* Copy link */}
        <button
          onClick={handleCopy}
          className={`text-[10px] font-pixel px-2 py-1 border transition-colors ${
            copied
              ? "border-neon-cyan/60 text-neon-cyan bg-neon-cyan/10"
              : "border-matrix-green/20 text-matrix-green/50 hover:text-neon-cyan hover:border-neon-cyan/40"
          }`}
          aria-label="Copy link to clipboard"
        >
          {copied ? "[ COPIED! ]" : "[ COPY LINK ]"}
        </button>
      </div>
    </div>
  );
}
