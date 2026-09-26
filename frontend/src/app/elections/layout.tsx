import type { Metadata } from "next";
import type { RaceSummary } from "@/types/election";
import { pageMetadata } from "@/lib/site";

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

// Canonical /elections. The state pages beneath set their own; the
// /elections/[raceId] route only redirects. See lib/site.ts.
export async function generateMetadata(): Promise<Metadata> {
  const cycleYear = await fetchCycleYear();
  const year = cycleYear ? `${cycleYear} ` : "";
  return pageMetadata({
    title: `${year}Elections by State: Senate, House & Ballot Measures`,
    description: `Every ${year}U.S. Senate and House race by state — candidates, FEC fundraising, partisan lean, and statewide ballot measures quoted from official sources.`,
    path: "/elections",
  });
}

export default function ElectionsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
