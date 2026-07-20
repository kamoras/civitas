"use client";

import { useCallback, useSyncExternalStore } from "react";

const KEY = "civitas_user_state";
// 'storage' only fires in OTHER tabs, so writes in this tab dispatch our own.
const EVENT = "civitas:userstate";

function subscribe(callback: () => void): () => void {
  window.addEventListener(EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

/**
 * The user's saved state code, backed by localStorage.
 *
 * Uses useSyncExternalStore instead of the read-into-state-in-an-effect
 * pattern: getServerSnapshot returns null so SSR/first paint match, then the
 * store surfaces the persisted value without a synchronous setState (and stays
 * in sync across tabs via the storage event).
 */
export function useUserState(): [string | null, (s: string | null) => void] {
  const state = useSyncExternalStore(
    subscribe,
    () => localStorage.getItem(KEY),
    () => null,
  );

  const setUserState = useCallback((s: string | null) => {
    if (s) localStorage.setItem(KEY, s);
    else localStorage.removeItem(KEY);
    window.dispatchEvent(new Event(EVENT));
  }, []);

  return [state, setUserState];
}
