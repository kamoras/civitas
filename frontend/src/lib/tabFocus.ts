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
