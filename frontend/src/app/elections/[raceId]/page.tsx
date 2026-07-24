import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { RaceDetail } from "@/types/election";
import RaceDetailClient from "./RaceDetailClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

async function fetchRace(raceId: string): Promise<RaceDetail | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/races/${encodeURIComponent(raceId)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function raceLabel(race: RaceDetail): string {
  if (race.office === "S") return `${race.state} Senate`;
  return race.district ? `${race.state}-${race.district} House` : `${race.state} House`;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ raceId: string }>;
}): Promise<Metadata> {
  const { raceId } = await params;
  const race = await fetchRace(raceId);

  const title = race ? `${raceLabel(race)} ${race.cycleYear} — Civitas` : "Race — Civitas";
  const description = race
    ? `${race.candidates.length} candidates, fundraising, and live coverage for the ${race.cycleYear} ${raceLabel(race)} race.`
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

export default async function RaceDetailPage({
  params,
}: {
  params: Promise<{ raceId: string }>;
}) {
  const { raceId } = await params;
  const race = await fetchRace(raceId);

  if (!race) notFound();

  return <RaceDetailClient race={race} />;
}
