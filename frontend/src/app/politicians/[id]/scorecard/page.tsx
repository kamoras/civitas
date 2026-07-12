import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { PoliticianProfile } from "@/types/politicians";
import FullScorecardClient from "./FullScorecardClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas.paramain.com";

async function fetchProfile(id: string): Promise<PoliticianProfile | null> {
  try {
    const res = await fetch(`${BACKEND}/api/politicians/${encodeURIComponent(id)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const profile = await fetchProfile(id);

  const name = profile?.identity.name ?? "Politician";
  const title = `${name} — Full Scorecard — Civitas`;
  const description = `Full scorecard for ${name} — funding, voting record, lobbying matches, and more on Civitas.`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/politicians/${id}/scorecard`,
      siteName: "Civitas",
    },
  };
}

export default async function FullScorecardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const profile = await fetchProfile(id);

  if (!profile) notFound();

  return <FullScorecardClient profile={profile} />;
}
