"use client";

import { useEffect, useState } from "react";
import { fetchLiveResults } from "@/lib/api";
import { showsResults } from "@/lib/results";
import type { ElectionPhaseInfo, LiveResults } from "@/types/election";

/** How often an open results page asks for the count. The count itself
 * moves on the backend's five-minute sync and nginx caches the route for
 * 30s, so a minute sees each sync land within about a minute. */
export const RESULTS_POLL_MS = 60_000;

/** How often a campaign page asks whether results have started, and only
 * while election day is close (electionIsNear): a page left open on the
 * eve of the election switches to the count on its own, and the rest of
 * the year a campaign page asks once or not at all. */
export const CAMPAIGN_POLL_MS = 10 * 60_000;

/** Waits after consecutive failed refreshes: an endpoint that keeps failing
 * is asked less and less often, not every minute forever. The last value
 * repeats. */
export const RETRY_BACKOFF_MS = [60_000, 2 * 60_000, 5 * 60_000, 10 * 60_000];

const HOUR = 3_600_000;

/** Whether election day is close enough that a page still in campaign mode
 * should keep asking: from 36 hours before the day (UTC midnight of the
 * date) until two days after it. Past that the phase has either turned or
 * names the next cycle's election, two years out. */
export function electionIsNear(
  phase: Pick<ElectionPhaseInfo, "electionDate"> | null | undefined,
  now: number = Date.now()
): boolean {
  if (!phase?.electionDate) return false;
  const day = Date.parse(`${phase.electionDate}T00:00:00Z`);
  if (Number.isNaN(day)) return false;
  return now >= day - 36 * HOUR && now < day + 48 * HOUR;
}

// setTimeout's ceiling (a signed 32-bit ms count, ~24.8 days): a longer
// wait fires at once. A page open for longer just asks again at the ceiling
// and re-arms.
const MAX_TIMEOUT_MS = 2 ** 31 - 1;

/** How long until election day becomes near (electionIsNear), capped at
 * setTimeout's ceiling — so a page opened days before can wake itself then.
 * Null when that moment has passed (the window is open, or over). */
export function msUntilNear(
  phase: Pick<ElectionPhaseInfo, "electionDate"> | null | undefined,
  now: number = Date.now()
): number | null {
  if (!phase?.electionDate) return null;
  const day = Date.parse(`${phase.electionDate}T00:00:00Z`);
  if (Number.isNaN(day)) return null;
  const wait = day - 36 * HOUR - now;
  return wait > 0 ? Math.min(wait, MAX_TIMEOUT_MS) : null;
}

/** "every minute", "every 2 minutes" — the retry wait, for a status line
 * that has to say how often the page is really asking. */
export function describeInterval(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / 60_000));
  return minutes === 1 ? "every minute" : `every ${minutes} minutes`;
}

/**
 * The live count, polled while the page shows results and the tab is
 * visible. Outside the results window one request answers "campaign" and
 * the next ask waits for election day to come near (msUntilNear); from then
 * it asks every CAMPAIGN_POLL_MS so an open page switches to results by
 * itself.
 * `enabled: false` asks nothing at all (a page that already knows from its
 * server render that there are no results). A failed request is retried
 * on a growing backoff (RETRY_BACKOFF_MS; `retryMs` says the current wait).
 * A background tab stops polling and catches up the moment it's shown
 * again, so a laptop left open overnight doesn't poll — except mid-backoff,
 * when it waits out the rest of the wait: a tab switch doesn't skip it.
 */
export function useLiveResults(
  state?: string,
  enabled = true
): {
  data: LiveResults | null;
  error: string | null;
  /** The wait before the next retry after a failure; null when the last
   * request succeeded. */
  retryMs: number | null;
} {
  const [data, setData] = useState<LiveResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryMs, setRetryMs] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // The wait for the next scheduled ask; null once there's nothing more
    // to ask (a campaign whose election has passed without results).
    let nextWait: number | null = null;
    // The next ask is the one election day's approach is due to make, not
    // a poll: a tab shown again doesn't bring it forward.
    let untilNear = false;
    // When the next ask is due (ms since epoch), so a tab shown again
    // mid-backoff waits out the rest rather than asking at once.
    let nextAt = 0;
    let failures = 0;

    const load = () => {
      fetchLiveResults(state)
        .then((next) => {
          if (cancelled) return;
          failures = 0;
          setData(next);
          setError(null);
          setRetryMs(null);
          const polling = showsResults(next.phase) || electionIsNear(next.phase);
          schedule(
            showsResults(next.phase)
              ? RESULTS_POLL_MS
              : polling
                ? CAMPAIGN_POLL_MS
                : msUntilNear(next.phase),
            !polling
          );
        })
        .catch((err: Error) => {
          if (cancelled) return;
          // Keep the last good count on screen; say the refresh failed.
          const wait = RETRY_BACKOFF_MS[Math.min(failures, RETRY_BACKOFF_MS.length - 1)];
          failures += 1;
          setError(err.message || "Could not refresh the results");
          setRetryMs(wait);
          schedule(wait);
        });
    };
    const schedule = (wait: number | null, isUntilNear = false) => {
      nextWait = wait;
      untilNear = isUntilNear;
      nextAt = wait != null ? Date.now() + wait : 0;
      if (timer) clearTimeout(timer);
      timer = null;
      if (wait != null && document.visibilityState === "visible") timer = setTimeout(load, wait);
    };
    const onVisible = () => {
      // Catch up only if the page was still asking. After a success a tab
      // shown again asks at once; after a failure only once the backoff's
      // wait is up, so a failing endpoint isn't asked again on every tab
      // switch; and a campaign page far from election day waits for the day
      // to come near, asking nothing on a tab switch.
      if (timer) clearTimeout(timer);
      timer = null;
      if (document.visibilityState !== "visible" || nextWait == null) return;
      const due = failures > 0 || untilNear ? nextAt - Date.now() : 0;
      if (due <= 0) load();
      else timer = setTimeout(load, due);
    };

    load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [state, enabled]);

  return { data, error, retryMs };
}
