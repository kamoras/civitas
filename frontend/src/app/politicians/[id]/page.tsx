import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { PoliticianProfile } from "@/types/politicians";
import PoliticianProfileClient from "./PoliticianProfileClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

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
  const role = profile?.identity.role ?? "";
  const state = profile?.identity.state ? `, ${profile.identity.state}` : "";
  const title = `${name} — Civitas`;
  const description = `${role}${state} — public record, scorecard, and active issues on Civitas.`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/politicians/${id}`,
      siteName: "Civitas",
    },
  };
}

export default async function PoliticianProfilePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const profile = await fetchProfile(id);

  if (!profile) notFound();

  return <PoliticianProfileClient profile={profile} />;
}
