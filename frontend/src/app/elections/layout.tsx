import type { Metadata } from "next";
import type { LiveResults, RaceSummary } from "@/types/election";
import { pageMetadata } from "@/lib/site";
import { describeElections } from "@/lib/seo";
import { showsResults } from "@/lib/results";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Cycle year for the title/description comes from the backend (same
// current_election_cycle() the /elections/races endpoint filters on) —
// not recomputed here — so this metadata rolls to the next cycle with
// zero code changes once the backend does.
async function fetchCycleYear(): Promise<number | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/races`, { next: { revalidate: 3600 } });
    if (!res.ok) return null;
    const races: RaceSummary[] = await res.json();
    return races[0]?.cycleYear ?? null;
  } catch {
    return null;
  }
}

// Whether /elections is in results mode (backend election_phase), read the
// way the page itself reads it. Revalidated every five minutes, so the
// description turns within minutes of the phase; an unreachable backend
// (as under `next build`) is the campaign wording, as the page's own
// default is.
async function fetchResultsMode(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/results`, { next: { revalidate: 300 } });
    if (!res.ok) return false;
    const body = (await res.json()) as Partial<LiveResults>;
    return showsResults(body?.phase);
  } catch {
    return false;
  }
}

// Canonical /elections. The state pages beneath set their own; the
// /elections/[raceId] route only redirects. See lib/site.ts.
export async function generateMetadata(): Promise<Metadata> {
  const [cycleYear, resultsMode] = await Promise.all([fetchCycleYear(), fetchResultsMode()]);
  return pageMetadata({ ...describeElections(cycleYear, resultsMode), path: "/elections" });
}

export default function ElectionsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
