import { connection } from "next/server";
import type { ResultsSummary } from "@/types/election";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer in the results window
// (api/elections.py CACHE_TTL_RESULTS_S): the count moves on a five-minute sync.
const REVALIDATE_S = 30;

/** GET /api/elections/results/summary, server-side: the election's phase
 * and, from election night, each counting state's line. Null when the
 * backend can't be asked, which no caller reads as a phase. One request
 * per render, however many parts of the homepage read it (Next memoizes
 * a fetch with the same URL and options within a render). */
export async function fetchResultsSummary(): Promise<ResultsSummary | null> {
  await connection();
  try {
    const res = await fetch(`${BACKEND}/api/elections/results/summary`, {
      next: { revalidate: REVALIDATE_S },
    });
    return res.ok ? ((await res.json()) as ResultsSummary) : null;
  } catch {
    return null;
  }
}
