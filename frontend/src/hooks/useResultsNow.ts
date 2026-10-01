import { useState } from "react";
import { useNow } from "@/hooks/useNow";
import { resultsNow } from "@/lib/results";
import type { LiveResults } from "@/types/election";

type Clock = NonNullable<LiveResults["clock"]>;

/**
 * The page's clock for judging the live count (lib/results resultsNow),
 * never running backwards.
 *
 * resultsNow alone can step back when a new answer arrives: its Date may be
 * behind where the last one had run on to (a copy with an older Date, two
 * servers a few seconds apart, a slow response), and polls that had closed
 * on screen would open again. So the page keeps where its clock stood each
 * time an answer or a failure replaced the one before, and never shows an
 * earlier time than that. Read once per page and handed down, so every part
 * of the page judges by the same clock.
 *
 * The floor applies only while there is a server clock: before the first
 * answer, resultsNow falls back to the browser's clock, and letting that set
 * the floor would pin a page whose browser runs hours fast to the browser's
 * time for good.
 *
 * `enabled` is useNow's: false reads the clock without re-rendering every
 * second.
 */
export function useResultsNow(
  results: Pick<LiveResults, "clock"> | null | undefined,
  failedAt: number | null,
  enabled = true
): number {
  const browserNow = useNow(enabled);
  const clock = results?.clock ?? null;
  // Where the clock stood when the answer or failure in hand replaced the
  // one before: updated only when either changes (React's "adjusting state
  // when a prop changes" pattern — not an effect, so the first render with
  // a new answer is already clamped, and not once a second).
  const [seen, setSeen] = useState<{
    clock: Clock | null;
    failedAt: number | null;
    floor: number | null;
  }>({ clock, failedAt, floor: null });
  let floor = seen.floor;
  if (seen.clock !== clock || seen.failedAt !== failedAt) {
    const at = seen.clock !== clock ? (clock?.receivedAt ?? browserNow) : (failedAt ?? browserNow);
    if (seen.clock) {
      const stood = resultsNow({ clock: seen.clock }, at, seen.failedAt);
      floor = floor == null ? stood : Math.max(floor, stood);
    }
    setSeen({ clock, failedAt, floor });
  }
  const now = resultsNow(results, browserNow, failedAt);
  return clock && floor != null ? Math.max(now, floor) : now;
}
