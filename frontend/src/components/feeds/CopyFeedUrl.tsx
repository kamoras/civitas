"use client";

import { CopyStatus, useCopyFeedback } from "@/hooks/useCopyFeedback";
import { BOXED_CONTROL } from "@/lib/controlStyles";

/**
 * A feed's full URL, as text a reader can select, and a button that copies
 * it — what a feed reader or a Discord bot's setup asks for. The URL stays
 * visible and selectable, so copying never depends on the clipboard API.
 */
export default function CopyFeedUrl({ url, label }: { url: string; label: string }) {
  const [copied, copy, feedback] = useCopyFeedback(1500, {
    copied: `${label} feed address copied.`,
    failed: "This browser wouldn't copy it. Select the address and copy it instead.",
  });
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
        // A fixed name: the result is announced by CopyStatus, since a
        // change to the focused button's own name usually isn't.
        aria-label={`Copy the ${label} feed address`}
      >
        {copied ? "[ COPIED! ]" : "[ COPY ]"}
      </button>
      <CopyStatus feedback={feedback} />
    </div>
  );
}
