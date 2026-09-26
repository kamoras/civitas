import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { PoliticianProfile } from "@/types/politicians";
import { usableRecord } from "@/lib/ssrPayload";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import { describeProfile, personJsonLd } from "@/lib/seo";
import PoliticianProfileClient from "./PoliticianProfileClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

async function fetchProfile(id: string): Promise<PoliticianProfile | null> {
  try {
    const res = await fetch(`${BACKEND}/api/politicians/${encodeURIComponent(id)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return usableRecord<PoliticianProfile>(await res.json(), "identity", "branch");
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
  const path = `/politicians/${encodeURIComponent(id)}`;

  if (!profile) {
    return pageMetadata({ title: "Politician not found", description: "No record for this id.", path, noindex: true });
  }

  const { title, description } = describeProfile(profile);
  const ogImage = absoluteUrl(`/api/og?politician=${encodeURIComponent(id)}`);
  return pageMetadata({
    title,
    description,
    path,
    type: "profile",
    images: [{ url: ogImage, width: 1200, height: 630, alt: profile.identity.name }],
  });
}

export default async function PoliticianProfilePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const profile = await fetchProfile(id);

  if (!profile) notFound();

  return (
    <>
      <JsonLd
        data={[
          personJsonLd(id, profile),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Politicians", url: absoluteUrl("/politicians") },
            { name: profile.identity.name, url: absoluteUrl(`/politicians/${encodeURIComponent(id)}`) },
          ]),
        ]}
      />
      <PoliticianProfileClient profile={profile} />
    </>
  );
}
