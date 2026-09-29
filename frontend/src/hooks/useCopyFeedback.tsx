"use client";

import { useRef, useState } from "react";

/**
 * "Copy to clipboard, show confirmation, auto-reset" — was duplicated in
 * ShareButtons.tsx and the Action Center's call-script copier (removed
 * 2026-09) with inconsistent,
 * undocumented reset durations (1500ms vs 2000ms) and inconsistent
 * handling of rapid re-clicks (only one of the two cleared a pending
 * reset timer before starting a new one).
 *
 * `copy` resolves true once the text is on the clipboard, false if the
 * browser refused — it never rejects.
 *
 * `feedback` is for a `CopyStatus` live region beside the button. The button's own label changing is not enough:
 * screen readers generally don't announce a change to the name of the
 * control that has focus, and a refused copy changed nothing at all, so the
 * click was silent either way (WCAG 4.1.3). It is cleared first and set a
 * beat later, so a second copy is announced again rather than being the
 * text the region already holds.
 */
export function useCopyFeedback(
  ms = 1500,
  messages: { copied: string; failed: string } = {
    copied: "Copied.",
    failed: "This browser wouldn't copy it. Select the text and copy it instead.",
  }
): [boolean, (text: string) => Promise<boolean>, CopyFeedback] {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<CopyFeedback>({ status: "", failed: false });
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function announce(status: string, failed: boolean) {
    setFeedback({ status: "", failed });
    setTimeout(() => setFeedback({ status, failed }), 100);
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      announce(messages.copied, false);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => setCopied(false), ms);
      return true;
    } catch {
      // clipboard not available
      announce(messages.failed, true);
      return false;
    }
  }

  return [copied, copy, feedback];
}

export interface CopyFeedback {
  status: string;
  failed: boolean;
}

/** The live region for `useCopyFeedback`. A success is visually hidden,
 *  since the button already shows it; a refusal is shown, since otherwise
 *  nothing on screen changes at all. */
export function CopyStatus({ feedback }: { feedback: CopyFeedback }) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={feedback.failed ? "font-mono text-xs text-signal-amber" : "sr-only"}
    >
      {feedback.status}
    </span>
  );
}
