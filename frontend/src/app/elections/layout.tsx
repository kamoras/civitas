import type { Metadata } from "next";
import type { LiveResults, RaceSummary } from "@/types/election";
import { pageMetadata } from "@/lib/site";
import { describeElections } from "@/lib/seo";
import { everyLiveStateVoting, showsResults } from "@/lib/results";

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

// What /elections is showing (backend election_phase), read the way the
// page itself reads it: results once some covered state's last polls have
// closed; on election day before that, no count yet (everyLiveStateVoting)
// — and a link shared that morning must read neither "Election Results"
// nor the campaign's "partisan lean", which the page stops drawing on
// election day. Revalidated every five minutes, so the description turns
// within minutes of the phase or the first close; an unreachable backend
// (as under `next build`) is the campaign wording, as the page's own
// default is. `now` is this server's clock, which in production runs
// beside the backend's.
async function fetchMode(now: number): Promise<"campaign" | "election_day" | "results"> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/results`, { next: { revalidate: 300 } });
    if (!res.ok) return "campaign";
    const body = (await res.json()) as Partial<LiveResults>;
    if (!body?.phase || !showsResults(body.phase)) return "campaign";
    return everyLiveStateVoting(
      {
        phase: body.phase,
        pollsClose: body.pollsClose,
        feeds: body.feeds,
        races: Array.isArray(body.races) ? body.races : [],
        liveStates: Array.isArray(body.liveStates) ? body.liveStates : [],
      },
      now
    )
      ? "election_day"
      : "results";
  } catch {
    return "campaign";
  }
}

// Canonical /elections. The state pages beneath set their own; the
// /elections/[raceId] route only redirects. See lib/site.ts.
export async function generateMetadata(): Promise<Metadata> {
  const [cycleYear, mode] = await Promise.all([fetchCycleYear(), fetchMode(Date.now())]);
  return pageMetadata({ ...describeElections(cycleYear, mode), path: "/elections" });
}

export default function ElectionsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
