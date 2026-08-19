"use client";

import { useState, type KeyboardEvent } from "react";
import type { ActionIssue } from "@/types/action";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { BOXED_CONTROL } from "@/lib/controlStyles";

interface ShareButtonsProps {
  issue: ActionIssue;
  className?: string;
  /**
   * Override the URL being shared. Defaults to the Action Center deep link;
   * the standalone /issue/{id} full-story page passes its own URL so a share
   * from that page points back at the page the sharer was actually reading.
   */
  shareUrl?: string;
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

export default function ShareButtons({
  issue,
  className = "",
  shareUrl: shareUrlOverride,
}: ShareButtonsProps) {
  const shareUrl = shareUrlOverride ?? `https://civitas-research.org/action?issue=${issue.id}`;
  const shareText = buildShareText(issue.title, shareUrl);
  const encodedText = encodeURIComponent(shareText);

  const [mastodonInstance, setMastodonInstance] = useState("mastodon.social");
  const [showMastodonInput, setShowMastodonInput] = useState(false);
  const [copied, copy] = useCopyFeedback(1500);

  function handleCopy() {
    copy(shareUrl);
  }

  function handleMastodonShare() {
    const instance = mastodonInstance.trim().replace(/^https?:\/\//, "");
    if (!instance) return;
    const url = `https://${instance}/share?text=${encodedText}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  return (
    <div className={`pt-4 border-t border-white/[0.07] ${className}`}>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-xs font-mono text-ink-min mr-1">SHARE:</span>

        {/* X / Twitter */}
        <a
          href={`https://x.com/intent/tweet?text=${encodedText}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-mono px-2 py-1 border border-white/[0.07] text-ink-lo hover:text-phos hover:border-signal-cyan/40 transition-colors bg-transparent"
          aria-label="Share on X (Twitter)"
        >
          [ X ]
        </a>

        {/* Bluesky */}
        <a
          href={`https://bsky.app/intent/compose?text=${encodedText}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-mono px-2 py-1 border border-white/[0.07] text-ink-lo hover:text-phos hover:border-signal-cyan/40 transition-colors bg-transparent"
          aria-label="Share on Bluesky"
        >
          [ BSKY ]
        </a>

        {/* Mastodon — toggle inline form */}
        {!showMastodonInput ? (
          <button
            onClick={() => setShowMastodonInput(true)}
            className="text-xs font-mono px-2 py-1 border border-white/[0.07] text-ink-lo hover:text-phos hover:border-signal-cyan/40 transition-colors"
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
              className="text-xs font-mono bg-surface-base border border-white/15 text-signal-cyan px-2 py-1 w-32 focus:outline-none focus:border-signal-cyan/40"
              autoFocus
            />
            <button
              onClick={handleMastodonShare}
              className="text-xs font-mono px-2 py-1 border border-white/15 text-signal-cyan hover:text-phos hover:border-signal-cyan/40 transition-colors"
              aria-label="Open Mastodon share"
            >
              GO
            </button>
            <button
              onClick={() => setShowMastodonInput(false)}
              className="text-xs font-mono text-ink-min hover:text-phos transition-colors px-1"
              aria-label="Cancel Mastodon share"
            >
              ✕
            </button>
          </span>
        )}

        {/* Copy link */}
        <button
          onClick={handleCopy}
          className={`text-xs font-mono px-2 py-1 border transition-colors ${
            copied
              ? BOXED_CONTROL.selected
              : BOXED_CONTROL.unselected
          }`}
          aria-label="Copy link to clipboard"
        >
          {copied ? "[ COPIED! ]" : "[ COPY LINK ]"}
        </button>
      </div>
    </div>
  );
}
