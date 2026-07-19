"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}

// Generic centered-overlay dialog — first Modal/Dialog component in the
// codebase (previously every "more detail" surface was squeezed into an
// inline expand/collapse or MetricTooltip's small anchored popover, which
// is fine for a one-line hint but unreadable for anything longer, like
// score-breakdown math). Portaled to document.body so it isn't clipped by
// an ancestor's narrow/overflow-hidden container the way an inline panel
// nested in a `w-48` column would be.
export default function Modal({ open, onClose, title, children }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // Focus trap: keep Tab cycling within the dialog instead of
      // leaking out to whatever's behind the backdrop. Content here is
      // simple (a close button plus static text), so a fresh query on
      // every keypress is cheap and always current — no need to cache it.
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/80" aria-hidden="true" onMouseDown={onClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className="relative z-10 w-full max-w-lg max-h-[85vh] overflow-y-auto border border-matrix-green/30 bg-crt-black shadow-2xl outline-none"
      >
        {title && (
          <div className="sticky top-0 flex items-center justify-between gap-3 border-b border-matrix-green/20 bg-crt-black px-4 py-3">
            <h3 className="text-neon-cyan text-sm tracking-widest">{title.toUpperCase()}</h3>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="text-matrix-green/50 hover:text-matrix-green transition-colors text-lg leading-none px-1 shrink-0"
            >
              ×
            </button>
          </div>
        )}
        <div className="p-4 text-sm text-matrix-green/80 leading-relaxed">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
