import { useCallback, useSyncExternalStore } from "react";

/**
 * A credential held in `sessionStorage`, read as an external store.
 *
 * sessionStorage is not React state — it survives re-mounts, it is shared by
 * everything on the page, and it cannot be read while prerendering. Subscribing
 * to it rather than copying it into state on mount means there is exactly one
 * answer to "am I signed in", and signing out in one place is visible
 * everywhere without a broadcast of props.
 *
 * `ready` is false for the prerender and the hydration pass, when the store
 * genuinely cannot be read; callers use it to render nothing rather than flash
 * a sign-in form at someone who is already signed in.
 */

const listeners = new Map<string, Set<() => void>>();
const cache = new Map<string, string | null>();

function readKey(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    // Safari in private mode, and any embedding that blocks storage access.
    return null;
  }
}

function writeKey(key: string, value: string | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    // Non-fatal: the value still lives in the cache for this page's lifetime.
  }
  cache.set(key, value);
  listeners.get(key)?.forEach((notify) => notify());
}

export interface SessionValue {
  value: string | null;
  /** False until the client can actually read storage. */
  ready: boolean;
  set: (value: string | null) => void;
}

/**
 * A value held in `sessionStorage` for the open tab only: gone when the tab
 * closes, never sent anywhere. The store behind both the admin token and
 * the reader's state choice (useYourState).
 */
export function useSessionValue(key: string): SessionValue {
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      let forKey = listeners.get(key);
      if (!forKey) {
        forKey = new Set();
        listeners.set(key, forKey);
      }
      forKey.add(onStoreChange);
      cache.set(key, readKey(key));

      // Another tab clearing the same key should sign this one out too.
      const onStorage = (e: StorageEvent) => {
        if (e.key === null || e.key === key) {
          cache.set(key, readKey(key));
          onStoreChange();
        }
      };
      window.addEventListener("storage", onStorage);

      return () => {
        forKey.delete(onStoreChange);
        if (forKey.size === 0) listeners.delete(key);
        window.removeEventListener("storage", onStorage);
      };
    },
    [key]
  );

  const getSnapshot = useCallback(() => {
    if (!cache.has(key)) cache.set(key, readKey(key));
    return cache.get(key) ?? null;
  }, [key]);

  // No storage exists during the prerender, and hydration has to render the
  // same thing the server did. `ready` flips on the first post-hydration read.
  const getServerSnapshot = useCallback(() => null, []);

  const value = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const ready = useSyncExternalStore(subscribe, alwaysReady, neverReady);
  const set = useCallback((next: string | null) => writeKey(key, next), [key]);
  return { value, ready, set };
}

export interface SessionToken {
  token: string | null;
  /** False until the client can actually read storage. */
  ready: boolean;
  signIn: (token: string) => void;
  signOut: () => void;
}

export function useSessionToken(key: string): SessionToken {
  const { value, ready, set } = useSessionValue(key);
  const signIn = useCallback((next: string) => set(next), [set]);
  const signOut = useCallback(() => set(null), [set]);
  return { token: value, ready, signIn, signOut };
}

const alwaysReady = () => true;
const neverReady = () => false;

/** Test seam: forgets every cached key so suites don't bleed into each other. */
export function __resetSessionTokenCache(): void {
  listeners.clear();
  cache.clear();
}
