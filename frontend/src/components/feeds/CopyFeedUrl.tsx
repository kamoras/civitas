"use client";

import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { BOXED_CONTROL } from "@/lib/controlStyles";

/**
 * A feed's full URL, as text a reader can select, and a button that copies
 * it — what a feed reader or a Discord bot's setup asks for. The URL stays
 * visible and selectable, so copying never depends on the clipboard API.
 */
export default function CopyFeedUrl({ url, label }: { url: string; label: string }) {
  const [copied, copy] = useCopyFeedback(1500);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <code className="min-w-0 break-all border border-white/[0.07] bg-white/[0.02] px-2 py-1 font-mono text-xs text-ink-hi">
        {url}
      </code>
      <button
        type="button"
        onClick={() => copy(url)}
        className={`shrink-0 border px-2 py-1 font-mono text-xs transition-colors ${
          copied ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
        }`}
        // The visible "COPIED!" is the only confirmation; a fixed label
        // would hide it from screen readers.
        aria-label={copied ? `${label} feed address copied` : `Copy the ${label} feed address`}
      >
        {copied ? "[ COPIED! ]" : "[ COPY ]"}
      </button>
    </div>
  );
}
