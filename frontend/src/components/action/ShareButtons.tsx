"use client";

import { useState, type KeyboardEvent } from "react";
import type { ActionIssue } from "@/types/action";
import { CopyStatus, useCopyFeedback } from "@/hooks/useCopyFeedback";
import { absoluteUrl, BSKY_HANDLE } from "@/lib/site";
import { SHARE_EXCLUDE_ATTR, issueShareMeta } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";

interface ShareButtonsProps {
  issue: ActionIssue;
  className?: string;
  /**
   * Override the URL being shared. Defaults to the issue's own page,
   * /issue/{id}: server-rendered and indexable, where an Action Center deep
   * link only resolves while the issue is still on a day's board.
   */
  shareUrl?: string;
  /**
   * Offer an image of the issue card this row sits in. Off where the row
   * isn't inside a card (the full-story page, whose sections have their
   * own Share buttons) — decided here, on the server, rather than by the
   * button finding no card after hydration and vanishing.
   */
  imageShare?: boolean;
}

/**
 * The post text for a share intent. `handle` credits Civitas's own account,
 * and only Bluesky has one: the text used to say "via @civitasvote" on every
 * network, a handle that is nobody's on Bluesky and not Civitas's anywhere.
 */
export function buildShareText(title: string, shareUrl: string, handle?: string): string {
  const base = `${title}. The record and what you can do: ${shareUrl}`;
  const via = handle ? ` via @${handle}` : "";
  for (const text of [`${base}${via} #CivicTransparency`, `${base}${via}`, base]) {
    if (text.length <= 240) return text;
  }
  // Hard trim as last resort
  return base.slice(0, 237) + "...";
}

export default function ShareButtons({
  issue,
  className = "",
  shareUrl: shareUrlOverride,
  imageShare = true,
}: ShareButtonsProps) {
  const shareUrl = shareUrlOverride ?? absoluteUrl(`/issue/${issue.publicId}`);
  const encodedText = encodeURIComponent(buildShareText(issue.title, shareUrl));
  const encodedBskyText = encodeURIComponent(buildShareText(issue.title, shareUrl, BSKY_HANDLE));

  const [mastodonInstance, setMastodonInstance] = useState("mastodon.social");
  const [showMastodonInput, setShowMastodonInput] = useState(false);
  const [copied, copy, copyFeedback] = useCopyFeedback(1500, {
    copied: "Link copied.",
    failed: "This browser wouldn't copy the link.",
  });

  function handleCopy() {
    copy(shareUrl);
  }

  function handleMastodonShare() {
    const instance = mastodonInstance.trim().replace(/^https?:\/\//, "");
    if (!instance) return;
    const url = `https://${instance}/share?text=${encodedText}`;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  // 44px on a phone (mobile spec P10).
  const linkCls =
    "inline-flex min-h-11 items-center sm:min-h-0 font-mono text-xs text-ink-lo underline decoration-white/20 underline-offset-4 transition-colors hover:text-ink-hi";

  return (
    <div
      className={`flex flex-wrap items-baseline gap-x-3 gap-y-2 font-mono text-xs text-ink-min ${className}`}
      {...{ [SHARE_EXCLUDE_ATTR]: "" }}
    >
      <span>Share</span>

      <a
        href={`https://x.com/intent/tweet?text=${encodedText}`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkCls}
        aria-label="Share on X (opens in new tab)"
      >
        X
      </a>

      <a
        href={`https://bsky.app/intent/compose?text=${encodedBskyText}`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkCls}
        aria-label="Share on Bluesky (opens in new tab)"
      >
        Bluesky
      </a>

      {/* Mastodon has no single host, so it asks which instance first. */}
      {!showMastodonInput ? (
        <button
          onClick={() => setShowMastodonInput(true)}
          className={linkCls}
          aria-label="Share on Mastodon"
        >
          Mastodon
        </button>
      ) : (
        <span className="flex items-baseline gap-2">
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
            className="w-36 border border-white/15 bg-surface-base px-2 py-1 font-mono text-xs text-ink-hi focus:border-ink-lo focus:outline-none"
            autoFocus
          />
          <button onClick={handleMastodonShare} className={linkCls}>
            Share
          </button>
          <button
            onClick={() => setShowMastodonInput(false)}
            className={linkCls}
            aria-label="Cancel Mastodon share"
          >
            Cancel
          </button>
        </span>
      )}

      {/* An image of the issue card this row sits in (its enclosing
          `data-share-section`). The card names the issue itself, so no
          title strip. Labelled by the issue, so each card's button has
          its own name. */}
      {imageShare && (
        <ShareSubjectProvider
          subject={{ title: issue.title, url: shareUrl, ...issueShareMeta(issue) }}
        >
          <ShareSectionButton
            label={issue.title}
            withStrip={false}
            anchor={null}
            className={linkCls}
          >
            Image
          </ShareSectionButton>
        </ShareSubjectProvider>
      )}

      <button
        onClick={handleCopy}
        className={linkCls}
        // A fixed name: the result is announced by CopyStatus, since a
        // change to the focused button's own name usually isn't.
        aria-label="Copy link to clipboard"
      >
        {copied ? "Copied" : "Copy link"}
      </button>
      <CopyStatus feedback={copyFeedback} />
    </div>
  );
}
