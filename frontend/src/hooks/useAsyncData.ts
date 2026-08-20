"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncData<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Re-runs the current request. For retry buttons — calling it from an event
   *  handler is exactly where a state change belongs. */
  retry: () => void;
}

/**
 * Fetch-on-key, without a synchronous setState in the effect body.
 *
 * Roughly twenty places wrote this by hand:
 *
 *     useEffect(() => {
 *       if (notMyTab) return;
 *       setLoading(true);
 *       setError(null);
 *       fetchThing(args).then(setData).catch(e => setError(e.message))
 *         .finally(() => setLoading(false));
 *     }, [deps]);
 *
 * The `setLoading(true)` runs synchronously inside the effect, so every mount
 * and every dependency change costs an extra render pass before the request
 * has even gone out — which is what react-hooks/set-state-in-effect flags.
 *
 * The fix is to stop STORING `loading` and derive it instead. State holds the
 * key its data belongs to; if that key is not the one being asked for, the
 * answer has not arrived and the hook is loading. Nothing is set until the
 * promise settles, so there is no cascading render, and stale data from a
 * previous key can never be shown as if it were current.
 *
 * `fetcher` is deliberately not a dependency — callers write it inline, so it
 * is a new function every render and would refetch forever. `key` is the
 * identity of the request and the only thing that triggers one. It must
 * therefore include every argument the fetcher closes over; a key that misses
 * an argument is a stale-data bug.
 *
 * Pass `null` for `fetcher` to disable the hook — this replaces the
 * `if (branch !== "house") return;` guards, and reports neither loading nor
 * data while disabled.
 */
export function useAsyncData<T>(key: string, fetcher: (() => Promise<T>) | null): AsyncData<T> {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    data: T | null;
    error: string | null;
  } | null>(null);

  // The attempt counter is folded into the key, so a retry is just a new
  // request identity and every rule below applies to it unchanged.
  const requestKey = `${key}#${attempt}`;

  // Kept in a ref so the fetch effect can call the latest closure without
  // taking it as a dependency. Synced in its own effect rather than during
  // render, because writing a ref while rendering is exactly the impurity the
  // sibling lint rule is about. Effects run in declaration order within a
  // commit, so this one has always run by the time the fetch effect below
  // reads it.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const enabled = fetcher !== null;

  // Already-settled results are kept when the hook is disabled and re-enabled
  // — switching to another tab and back must not refire the request. This is
  // what the hand-written `if (presEntries.length > 0) return;` guards were
  // doing, expressed once instead of per call site.
  const settledKey = result?.key;

  useEffect(() => {
    if (!enabled || settledKey === requestKey) return;
    let cancelled = false;
    const run = fetcherRef.current;
    if (!run) return;

    run()
      .then((data) => {
        if (!cancelled) setResult({ key: requestKey, data, error: null });
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setResult({
            key: requestKey,
            data: null,
            error: e instanceof Error ? e.message : "Request failed",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [requestKey, enabled, settledKey]);

  // Stable identity: callers put `retry` in effect dependency arrays (a poll
  // interval, a focus listener), and a fresh function each render would tear
  // those down and rebuild them on every single render.
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const fresh = settledKey === requestKey ? result : null;
  return {
    data: fresh?.data ?? null,
    error: fresh?.error ?? null,
    loading: enabled && fresh === null,
    retry,
  };
}
