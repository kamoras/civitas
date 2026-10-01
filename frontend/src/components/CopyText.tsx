"use client";

import { CopyStatus, useCopyFeedback } from "@/hooks/useCopyFeedback";
import { BOXED_CONTROL } from "@/lib/controlStyles";

/**
 * One line of text to paste somewhere else — a feed's address, an API
 * endpoint, a setup command — shown selectable, with a button that copies
 * it. The text stays on screen, so copying never depends on the clipboard
 * API. `label` names what it is ("Elections feed address").
 */
export default function CopyText({ text, label }: { text: string; label: string }) {
  const [copied, copy, feedback] = useCopyFeedback(1500, {
    copied: `${label} copied.`,
    failed: "This browser wouldn't copy it. Select the address and copy it instead.",
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <code className="min-w-0 break-all border border-white/[0.07] bg-white/[0.02] px-2 py-1 font-mono text-xs text-ink-hi">
        {text}
      </code>
      <button
        type="button"
        onClick={() => copy(text)}
        className={`shrink-0 border px-2 py-1 font-mono text-xs transition-colors ${
          copied ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
        }`}
        // A fixed name: the result is announced by CopyStatus, since a
        // change to the focused button's own name usually isn't.
        aria-label={`Copy the ${label}`}
      >
        {copied ? "[ COPIED! ]" : "[ COPY ]"}
      </button>
      <CopyStatus feedback={feedback} />
    </div>
  );
}
