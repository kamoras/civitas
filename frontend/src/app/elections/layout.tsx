import type { Metadata } from "next";
import type { RaceSummary } from "@/types/election";

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

export async function generateMetadata(): Promise<Metadata> {
  const cycleYear = await fetchCycleYear();
  const title = cycleYear ? `${cycleYear} Midterm Elections — Civitas` : "Midterm Elections — Civitas";
  const listDescription = cycleYear
    ? `Every ${cycleYear} Senate and House race`
    : "Every Senate and House race";

  return {
    title,
    description: `${listDescription} — candidates, FEC fundraising totals, partisan lean, and live news coverage, all sourced from public federal data.`,
    openGraph: {
      title,
      description: `Track ${listDescription.toLowerCase()}: candidates, fundraising, partisan lean, and live coverage.`,
      url: "https://civitas-research.org/elections",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: `Track ${listDescription.toLowerCase()}: candidates, fundraising, and live coverage.`,
    },
  };
}

export default function ElectionsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
