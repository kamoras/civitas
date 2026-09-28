import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { PoliticianProfile } from "@/types/politicians";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";
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

/** A member's score, component by component, with the numbers each
 * sentence on the scorecard states. Fetched here, beside the profile, so the
 * scorecard renders whole on first paint instead of opening panels one click
 * at a time. Null when it fails: the scorecard then shows the scores alone. */
async function fetchBreakdown(
  branch: string,
  id: string
): Promise<RepresentationScoreBreakdown | null> {
  const segment =
    branch === "senate" ? "senators" : branch === "house" ? "representatives" : branch === "president" ? "presidents" : null;
  if (!segment) return null;
  try {
    const res = await fetch(`${BACKEND}/api/${segment}/${encodeURIComponent(id)}/score-breakdown`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    const body = await res.json();
    return body && typeof body === "object" ? (body as RepresentationScoreBreakdown) : null;
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
    return pageMetadata({
      title: "Politician not found",
      description: "No record for this id.",
      path,
      noindex: true,
    });
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
  const breakdown = await fetchBreakdown(profile.branch, id);

  return (
    <>
      <JsonLd
        data={[
          personJsonLd(id, profile),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Politicians", url: absoluteUrl("/politicians") },
            {
              name: profile.identity.name,
              url: absoluteUrl(`/politicians/${encodeURIComponent(id)}`),
            },
          ]),
        ]}
      />
      <PoliticianProfileClient profile={profile} breakdown={breakdown} />
    </>
  );
}
