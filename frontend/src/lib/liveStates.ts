/**
 * Which states Civitas reads a live election-night count for, as prose for
 * the about pages. Read from the results endpoint's own `liveStates` (the
 * backend's live_results_states(), from state_candidate_sources.json) at
 * render time, never typed into the page: a hand-typed "fifteen states"
 * and its list went stale the day a state was added.
 */
import { STATE_NAMES } from "@/lib/stateCodes";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** How often the about pages re-read the list. It changes only when a
 * state's results feed is configured, but `next build` can't reach the
 * backend, so each deploy prerenders the no-list wording: a short window
 * keeps that from standing for long. Match the pages' `revalidate`. */
export const LIVE_STATES_REVALIDATE_S = 300;

/** The state codes read live, A→Z; null when the backend couldn't be
 * asked (as under `next build`) or answered without a list. */
export async function fetchLiveStates(): Promise<string[] | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/results`, {
      next: { revalidate: LIVE_STATES_REVALIDATE_S },
    });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    const live = (body as { liveStates?: unknown } | null)?.liveStates;
    if (!Array.isArray(live)) return null;
    return live.filter((s): s is string => typeof s === "string" && s in STATE_NAMES).sort();
  } catch {
    return null;
  }
}

const WORDS = [
  "no",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
  "ten",
  "eleven",
  "twelve",
  "thirteen",
  "fourteen",
  "fifteen",
  "sixteen",
  "seventeen",
  "eighteen",
  "nineteen",
  "twenty",
];

/** "fifteen", "23" — a count as the prose writes it. */
export function countWord(n: number): string {
  return WORDS[n] ?? String(n);
}

/** "Arkansas, Colorado and Georgia" — names A→Z by name, "and" before the
 * last. */
export function stateNameList(codes: string[]): string {
  const names = codes.map((c) => STATE_NAMES[c] ?? c).sort((a, b) => a.localeCompare(b));
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}
