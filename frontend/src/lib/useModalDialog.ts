"use client";

import { useEffect, useRef, type RefObject } from "react";
import { inOtherDialog, trapTab } from "./focusedDialog";

/** What makes a panel a real modal dialog: while it is mounted the page
 * behind can't scroll, Escape closes it, Tab stays inside it, and when it
 * closes focus goes back to whatever opened it. Shared by the ballot's
 * contest drawer and the member scorecard's drawer. Returns the ref to put
 * on the panel. */
export function useModalDialog(onClose: () => void): RefObject<HTMLDivElement | null> {
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);

  // Opened: remember what had focus, lock the page behind. Closed: unlock
  // and hand focus back.
  useEffect(() => {
    opener.current = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
      if (opener.current instanceof HTMLElement) opener.current.focus();
    };
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (inOtherDialog(e, panel.current)) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (!panel.current) return;
      const focusable = panel.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      trapTab(
        e,
        Array.from(focusable).filter((el) => !el.closest("[hidden]"))
      );
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return panel;
}
