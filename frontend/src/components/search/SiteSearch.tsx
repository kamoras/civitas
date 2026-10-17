"use client";

import { useEffect, useId, useRef, useState } from "react";
import SearchCombobox from "./SearchCombobox";
import { trapTab } from "@/lib/focusedDialog";

/**
 * The header's way into site search: a link to /search (which works with
 * no JavaScript at all) that, with it, opens the search in a modal
 * `<dialog>` instead. The native modal makes the rest of the page inert,
 * so focus stays inside; Escape closes it (after the combobox has used its
 * own Escapes) and focus returns to this button. Full screen on a phone, a
 * panel under the header from 640 px.
 */
export default function SiteSearch({ className = "" }: { className?: string }) {
  // The header has two (the row's, and MENU's beside it): ids per instance.
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLAnchorElement>(null);
  // Mounted only while open, so each opening starts empty and focused.
  const [open, setOpen] = useState(false);
  // Closed by choosing a match: focus goes with the reader to the new
  // page, not back to this button.
  const navigating = useRef(false);

  // The modal makes the page behind it inert, but Tab past the last
  // control would still reach the browser's own toolbar: wrap it with the
  // site's shared trap (lib/focusedDialog.ts), as its other dialogs do.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      trapTab(
        e,
        [...dialog.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input")].filter(
          (el) => !el.closest("[hidden]")
        )
      );
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  function show(e: React.MouseEvent) {
    const dialog = dialogRef.current;
    if (!dialog || typeof dialog.showModal !== "function") return; // the link does the job
    e.preventDefault();
    setOpen(true);
    dialog.showModal();
  }

  function close() {
    dialogRef.current?.close();
  }

  return (
    <>
      <a
        ref={triggerRef}
        href="/search"
        onClick={show}
        aria-haspopup="dialog"
        className={`inline-flex items-center font-mono uppercase tracking-widest text-ink-lo transition-colors hover:text-ink-hi motion-reduce:transition-none ${className}`}
      >
        Search
      </a>
      <dialog
        ref={dialogRef}
        aria-labelledby={titleId}
        onClose={() => {
          setOpen(false);
          if (!navigating.current) triggerRef.current?.focus();
          navigating.current = false;
        }}
        className="m-0 h-full max-h-none w-full max-w-none bg-surface-base p-0 text-ink-hi backdrop:bg-surface-base/80 sm:mx-auto sm:mt-[12vh] sm:h-auto sm:max-w-2xl sm:border-2 sm:border-ink-hi"
      >
        <div className="flex flex-col gap-4 p-4 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-x-4 border-b-2 border-white/15 pb-3">
            <h2 id={titleId} className="font-pixel text-xs tracking-widest text-ink-hi">
              SEARCH
            </h2>
            <button
              type="button"
              onClick={close}
              className="inline-flex min-h-11 min-w-11 max-w-full flex-wrap items-center justify-center px-2 font-mono text-xs uppercase tracking-widest text-ink-lo hover:text-ink-hi"
            >
              Close <span className="sr-only">search</span>
              <span aria-hidden="true" className="ml-1 text-ink-min">
                [Esc]
              </span>
            </button>
          </div>
          {open && (
            <SearchCombobox
              autoFocus
              inlineList
              label="Search the whole site"
              onNavigate={() => {
                navigating.current = true;
                close();
              }}
            />
          )}
        </div>
      </dialog>
    </>
  );
}
