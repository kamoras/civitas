import { Metadata } from "next";
import { notFound } from "next/navigation";
import { raceTitleLabel } from "@/lib/elections";
import type { RaceDetail } from "@/types/election";
import { usableRecord } from "@/lib/ssrPayload";
import RaceDetailClient from "./RaceDetailClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

async function fetchRace(raceId: string): Promise<RaceDetail | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/races/${encodeURIComponent(raceId)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return usableRecord<RaceDetail>(await res.json(), "id", "state");
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ raceId: string }>;
}): Promise<Metadata> {
  const { raceId } = await params;
  const race = await fetchRace(raceId);

  const title = race ? `${raceTitleLabel(race)} ${race.cycleYear} — Civitas` : "Race — Civitas";
  const description = race
    ? `${race.candidates.length} ${race.candidates.length === 1 ? "candidate" : "candidates"}, fundraising, and live coverage for the ${race.cycleYear} ${raceTitleLabel(race)} race.`
    : "Election race detail on Civitas.";

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/elections/${raceId}`,
      siteName: "Civitas",
    },
  };
}

export default async function RaceDetailPage({ params }: { params: Promise<{ raceId: string }> }) {
  const { raceId } = await params;
  const race = await fetchRace(raceId);

  if (!race) notFound();

  return <RaceDetailClient race={race} />;
}
