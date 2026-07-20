import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(callback: () => void): () => void {
  const mq = window.matchMedia(QUERY);
  mq.addEventListener("change", callback);
  return () => mq.removeEventListener("change", callback);
}

function getSnapshot(): boolean {
  return window.matchMedia(QUERY).matches;
}

/**
 * Whether the user has requested reduced motion.
 *
 * Uses useSyncExternalStore rather than the read-into-state-in-an-effect
 * pattern (which React 19's react-hooks/set-state-in-effect rule flags and
 * which flashes the wrong value before the effect runs): getServerSnapshot
 * returns false so SSR/first paint assume motion-on, and the store keeps
 * React in sync with the media query without a synchronous setState.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
