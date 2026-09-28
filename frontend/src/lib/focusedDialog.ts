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
