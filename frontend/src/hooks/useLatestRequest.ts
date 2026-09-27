"use client";

import { useCallback, useRef, useState } from "react";

export interface LatestRequest<T, K> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** The key of the request most recently made — what the controls show
   *  while it is in flight. */
  requested: K;
  /** The key the data on screen was fetched with. */
  shown: K;
  request: (key: K, run: () => Promise<T>) => void;
}

/**
 * A list whose filter or page the reader changes, keeping what is on screen
 * until the change lands (unlike useAsyncData, which drops data whose key
 * is no longer current).
 *
 * Only the newest request may land: two quick clicks can resolve out of
 * order, and the older answer must not overwrite the newer one. A failed
 * request leaves the data on screen, sets `error`, and returns `requested`
 * to `shown`, so the controls describe the list again.
 *
 * `key` names what a request asks for (a filter, a category); a page change
 * within one filter can reuse its key.
 */
export function useLatestRequest<T, K>(initialKey: K, errorLabel: string): LatestRequest<T, K> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [requested, setRequested] = useState<K>(initialKey);
  const [shown, setShown] = useState<K>(initialKey);
  const seq = useRef(0);
  // `shown` for the failure path, which runs in a closure from an earlier
  // render.
  const shownRef = useRef<K>(initialKey);

  const request = useCallback(
    (key: K, run: () => Promise<T>) => {
      const mine = ++seq.current;
      setRequested(key);
      setLoading(true);
      setError(null);
      // Through a promise, so a fetcher that throws synchronously fails like
      // one that rejects.
      new Promise<T>((resolve) => resolve(run()))
        .then((result) => {
          if (mine !== seq.current) return;
          shownRef.current = key;
          setShown(key);
          setData(result);
        })
        .catch((e: unknown) => {
          if (mine !== seq.current) return;
          setRequested(shownRef.current);
          setError(e instanceof Error ? e.message : errorLabel);
        })
        .finally(() => {
          if (mine === seq.current) setLoading(false);
        });
    },
    [errorLabel]
  );

  return { data, loading, error, requested, shown, request };
}
