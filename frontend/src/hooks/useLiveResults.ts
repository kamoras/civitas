"use client";

import { useEffect, useState } from "react";
import { fetchLiveResults } from "@/lib/api";
import { showsResults } from "@/lib/results";
import type { LiveResults } from "@/types/election";

/** How often an open results page asks for the count. The count itself
 * moves on the backend's five-minute sync and nginx caches the route for
 * 30s, so a minute sees each sync land within about a minute. */
export const RESULTS_POLL_MS = 60_000;

/**
 * The live count, polled while the page shows results and the tab is
 * visible. Outside the results window one request answers "campaign" and
 * nothing more is asked; `enabled: false` asks nothing at all (a page that
 * already knows from its server render that there are no results). A background tab stops polling and catches up the
 * moment it's shown again, so a laptop left open overnight doesn't poll.
 */
export function useLiveResults(
  state?: string,
  enabled = true
): {
  data: LiveResults | null;
  error: string | null;
} {
  const [data, setData] = useState<LiveResults | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const load = () => {
      fetchLiveResults(state)
        .then((next) => {
          if (cancelled) return;
          setData(next);
          setError(null);
          if (showsResults(next.phase)) schedule();
        })
        .catch((err: Error) => {
          if (cancelled) return;
          // Keep the last good count on screen; say the refresh failed.
          setError(err.message || "Could not refresh the results");
          schedule();
        });
    };
    const schedule = () => {
      if (timer) clearTimeout(timer);
      if (document.visibilityState === "visible") timer = setTimeout(load, RESULTS_POLL_MS);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
      else if (timer) clearTimeout(timer);
    };

    load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [state, enabled]);

  return { data, error };
}
