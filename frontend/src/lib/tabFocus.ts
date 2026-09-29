// ~0.5s at 60fps: long enough for a History-synced selection to render.
const MAX_FRAMES = 30;

const selectedTab = (tablist: Element | null) =>
  tablist?.querySelector('[role="tab"][aria-selected="true"]') ?? null;

/**
 * Focus a tab once it has rendered as selected, on a later frame, after React
 * has updated the roving tabindex.
 *
 * Focus the incoming *tab*, never its panel: the Arrow/Home/End handler lives
 * on the tablist, so focus in the panel strands the keyboard after one press.
 *
 * Checking the tab once, on the next frame, is not enough. A selection
 * derived from the URL (the Action Center's `useSearchParams`) can render a
 * frame or more late, and a one-shot check dropped the focus there. Focusing
 * without checking is wrong too: a click, a Back press or another key may
 * have moved the selection on, and the stale frame would pull focus onto a
 * tabindex=-1 tab. So wait while the tab that was selected when this was
 * called is still selected, focus when the target is, and give up if any
 * other tab becomes selected.
 */
export function focusTabWhenSelected(tabId: string): void {
  const target = document.getElementById(tabId);
  const tablist = target?.closest('[role="tablist"]') ?? null;
  const before = selectedTab(tablist);
  let frames = 0;
  const check = () => {
    const now = selectedTab(tablist);
    if (now === target) target?.focus();
    else if (now === before && ++frames < MAX_FRAMES) requestAnimationFrame(check);
  };
  requestAnimationFrame(check);
}

/**
 * A retry button's click handler that keeps keyboard focus on the page.
 *
 * A retry swaps the error (and the focused button in it) for a loading line
 * and then the result, so the button unmounts and focus falls to <body>,
 * stranding a keyboard or screen-reader user at the top of the document.
 * Moving focus first to the enclosing tab panel (which is focusable, see the
 * tabs rule above) keeps it inside the region whose content is reloading.
 */
export function retryKeepingFocus(
  retry: () => void,
  /** A nearer home for focus than the panel, when the retry reloads only
   *  part of it (a monitor row's updates: back to that row's toggle). */
  target?: (button: Element) => HTMLElement | null | undefined
) {
  return (event: { currentTarget: Element }) => {
    const button = event.currentTarget;
    const home = target?.(button) ?? button.closest<HTMLElement>('[role="tabpanel"]');
    home?.focus({ preventScroll: true });
    retry();
  };
}

/**
 * After the selection changed without a key press or a click — Back or
 * Forward through tabs the page wrote to history — move focus to the newly
 * selected tab if it was left on another tab of the same tablist.
 *
 * Otherwise focus stays on a tab that is now tabindex=-1 and unselected, and
 * the tablist's arrow keys, which move from the *selected* tab, step from a
 * tab other than the one that has focus. Focus anywhere else is left alone:
 * a Back press from inside a panel doesn't pull focus to the tab bar.
 */
export function keepFocusOnSelectedTab(tabId: string): void {
  const target = document.getElementById(tabId);
  const focused = document.activeElement;
  if (!target || !focused || focused === target) return;
  if (focused.getAttribute("role") !== "tab") return;
  const tablist = target.closest('[role="tablist"]');
  if (tablist && tablist.contains(focused)) target.focus();
}
