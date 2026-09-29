import { useSyncExternalStore } from "react";

/**
 * The current time, re-read once a second.
 *
 * The wall clock is a mutable value React does not own, which is exactly what
 * `useSyncExternalStore` is for: reading it during render is impure (the same
 * render would produce a different answer a moment later), and reading it in
 * an effect means storing a copy that has to be kept in sync by hand.
 *
 * One ticker is shared by every subscriber, so two clocks on the same page
 * cannot disagree by a frame, and the interval stops when the last consumer
 * unmounts rather than running one timer per component.
 */

let current = 0;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  if (timer === null) {
    current = Date.now();
    timer = setInterval(() => {
      current = Date.now();
      for (const listener of listeners) listener();
    }, 1000);
  }
  return () => {
    listeners.delete(onStoreChange);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  // Advanced by the ticker while anyone subscribes. With no ticker running
  // (no subscriber yet, or only disabled readers) it is brought up to date
  // on read, but at most once a second: returning a fresh Date.now() on
  // every call would make React see a changed snapshot on every render and
  // loop forever, while a value left from when the last ticker stopped
  // could be hours old — the first render of a reader just enabled would
  // show that stale time for a frame.
  if (timer === null && Date.now() - current >= 1000) current = Date.now();
  return current;
}

// There is no clock to read while prerendering: the server's "now" would be
// baked into the HTML and then contradicted the instant the page hydrated.
// Zero renders as a placeholder, and the first client snapshot replaces it.
function getServerSnapshot(): number {
  return 0;
}

function subscribeNever(): () => void {
  return () => {};
}

/**
 * `enabled = false` reads the clock without subscribing to it: the caller
 * gets a time but is not re-rendered every second. A page that only needs the
 * clock in one phase (election night) passes that condition, so it doesn't
 * re-render its whole tree once a second for the rest of the year.
 */
export function useNow(enabled = true): number {
  return useSyncExternalStore(enabled ? subscribe : subscribeNever, getSnapshot, getServerSnapshot);
}

/** Test seam: drops the shared ticker so suites don't leak timers into each other. */
export function __resetNowTicker(): void {
  listeners.clear();
  if (timer !== null) clearInterval(timer);
  timer = null;
  current = 0;
}
