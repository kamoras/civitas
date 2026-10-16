/**
 * Whether a key event belongs to a dialog other than `own` — one opened from
 * inside it, like the share-image dialog over a drawer. Both listen on the
 * document, so without this check one Escape closed both. Focus that has
 * fallen to <body> still counts as `own`'s, so Escape keeps working there.
 */
export function inOtherDialog(e: KeyboardEvent, own: HTMLElement | null): boolean {
  if (!own || !(e.target instanceof Element)) return false;
  const dialog = e.target.closest('[role="dialog"]');
  return dialog !== null && dialog !== own && !own.contains(dialog);
}

/**
 * Keeps Tab inside a modal: from the last control to the first and back.
 *
 * Also from anywhere that is not one of `focusable`, which is where every
 * dialog here starts: focus goes to the dialog itself or its heading
 * (tabIndex -1), neither first nor last, so a check for "on the first
 * control" let Shift+Tab walk straight out to the page behind (WCAG 2.4.3).
 */
export function trapTab(e: KeyboardEvent, focusable: HTMLElement[]): void {
  if (e.key !== "Tab" || focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement as HTMLElement | null;
  const inside = active !== null && focusable.includes(active);
  if (e.shiftKey && (active === first || !inside)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !inside)) {
    e.preventDefault();
    first.focus();
  }
}
