"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { inOtherDialog, trapTab } from "@/lib/focusedDialog";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}

// Generic overlay dialog — first Modal/Dialog component in the codebase
// (previously every "more detail" surface was squeezed into an inline
// expand/collapse or MetricTooltip's small anchored popover, which is fine
// for a one-line hint but unreadable for anything longer, like score-breakdown
// math). Portaled to document.body so it isn't clipped by an ancestor's
// narrow/overflow-hidden container the way an inline panel nested in a `w-48`
// column would be.
//
// Centered from 640px up; under it, a bottom sheet (mobile spec P7): anchored
// to the bottom edge within thumb reach, at most 85% of the screen, a square
// top edge with the 3px ledger rule. Closing is a 44px button, Escape or a tap
// on the backdrop; no drag handle, since dragging can't be the only way to
// close (WCAG 2.5.7). Focus moves to the title, stays inside, and goes back to
// whatever opened it.
export default function Modal({ open, onClose, title, children }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  // The latest onClose, read when a key is pressed: an inline arrow from the
  // opener is a new function every render, and re-running the effect for it
  // sent focus back to the opener and in again on every parent render.
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (inOtherDialog(e, dialogRef.current)) return;
      if (e.key === "Escape") {
        closeRef.current();
        return;
      }
      // Focus trap: keep Tab cycling within the dialog instead of
      // leaking out to whatever's behind the backdrop.
      if (!dialogRef.current) return;
      trapTab(e, [
        ...dialogRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
        ),
      ]);
    }
    document.addEventListener("keydown", handleKeyDown);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    (titleRef.current ?? dialogRef.current)?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-end justify-center sm:items-center sm:p-4">
      <div className="absolute inset-0 bg-black/80" aria-hidden="true" onMouseDown={onClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="relative z-10 max-h-[85svh] w-full overflow-y-auto border-t-3 border-ink-min/60 bg-surface-base shadow-2xl outline-none sm:max-h-[85vh] sm:max-w-lg sm:border sm:border-white/15"
      >
        {title && (
          <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-white/[0.07] bg-surface-base pl-4">
            <h2 ref={titleRef} tabIndex={-1} className="text-sm text-signal-cyan outline-none">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex min-h-11 shrink-0 items-center px-4 font-mono text-[13px] text-ink-lo transition-colors hover:text-phos"
            >
              <span aria-hidden="true">[ ×&nbsp;</span>Close<span aria-hidden="true">&nbsp;]</span>
            </button>
          </div>
        )}
        <div className="p-4 text-sm text-ink leading-relaxed">{children}</div>
      </div>
    </div>,
    document.body
  );
}
