"use client";

import { useRef, useState } from "react";

/**
 * "Copy to clipboard, show confirmation, auto-reset" — was duplicated in
 * ShareButtons.tsx and MyRepsTab.tsx's ContactScript with inconsistent,
 * undocumented reset durations (1500ms vs 2000ms) and inconsistent
 * handling of rapid re-clicks (only one of the two cleared a pending
 * reset timer before starting a new one).
 */
/** `copy` resolves true once the text is on the clipboard, false if the
 *  browser refused — it never rejects. */
export function useCopyFeedback(ms = 1500): [boolean, (text: string) => Promise<boolean>] {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => setCopied(false), ms);
      return true;
    } catch {
      // clipboard not available
      return false;
    }
  }

  return [copied, copy];
}
