import { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import type { PoliticianProfile } from "@/types/politicians";
import type { RepresentationScoreBreakdown } from "@/types/scoreBreakdown";
import type { PeerComparison } from "@/types/peers";
import type { PresidentPeers } from "@/types/presidentPeers";
import type { PresidentCharts } from "@/types/presidentCharts";
import { fetchRecord } from "@/lib/ssrPayload";
import { absoluteUrl, memberFeed, notFoundMetadata, pageMetadata } from "@/lib/site";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import { CARD_ALT, cardImage, describeProfile, personJsonLd } from "@/lib/seo";
import { recordTrail } from "@/lib/routes";
import ThisWeekStrip from "@/components/shared/ThisWeekStrip";
import PresidentialSupportLine from "@/components/presidentialSupport/PresidentialSupportLine";
import PoliticianProfileClient from "./PoliticianProfileClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** Null only when there is no such politician; an outage throws (fetchRecord). */
function fetchProfile(id: string): Promise<PoliticianProfile | null> {
  return fetchRecord<PoliticianProfile>(
    `${BACKEND}/api/politicians/${encodeURIComponent(id)}`,
    { next: { revalidate: 120 } },
    "identity",
    "branch"
  );
}

/** A member's score, component by component, with the numbers each
 * sentence on the scorecard states. Fetched here, beside the profile, so the
 * scorecard renders whole on first paint instead of opening panels one click
 * at a time. Null when it fails: the scorecard then shows the scores alone.
 * Not for a president, whose page shows no score (fetchPeers instead). */
async function fetchBreakdown(
  branch: string,
  id: string
): Promise<RepresentationScoreBreakdown | null> {
  const segment = branch === "senate" ? "senators" : branch === "house" ? "representatives" : null;
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

/** Where a member stands among the caucus, for the first view, or a
 *  president among the other presidencies; null on any failure (a member's
 *  section then fetches it in the browser). */
async function fetchPeers(
  branch: string,
  id: string
): Promise<PeerComparison | PresidentPeers | null> {
  if (branch !== "senate" && branch !== "house" && branch !== "president") return null;
  const path =
    branch === "president"
      ? `presidents/${encodeURIComponent(id)}/peers`
      : `${branch === "senate" ? "senators" : "representatives"}/${encodeURIComponent(id)}/peers?group=caucus`;
  try {
    const res = await fetch(`${BACKEND}/api/${path}`, {
      next: { revalidate: 120 },
    });
    return res.ok ? ((await res.json()) as PeerComparison | PresidentPeers) : null;
  } catch {
    return null;
  }
}

/** A president's charts; null on any failure (the page then draws none). */
async function fetchCharts(branch: string, id: string): Promise<PresidentCharts | null> {
  if (branch !== "president") return null;
  try {
    const res = await fetch(`${BACKEND}/api/presidents/${encodeURIComponent(id)}/charts`, {
      next: { revalidate: 120 },
    });
    return res.ok ? ((await res.json()) as PresidentCharts) : null;
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

  if (!profile) {
    return notFoundMetadata("Politician not found", "No record for this id.");
  }

  // The canonical is the record's own id: a renamed member's old URL
  // redirects there (below), and is never a second page.
  const path = `/politicians/${encodeURIComponent(profile.id)}`;
  const { title, description } = describeProfile(profile);
  return pageMetadata({
    title,
    description,
    path,
    type: "profile",
    images: cardImage(`politician=${encodeURIComponent(profile.id)}`, CARD_ALT.profile(profile)),
    // A member of Congress has a feed of their own; a president or justice
    // has none.
    feed:
      profile.branch === "senate" || profile.branch === "house"
        ? memberFeed(profile.id, profile.identity.name)
        : undefined,
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
  // A member id renamed since the URL was published or indexed (the
  // backend answers the old id with the member under their current id):
  // a permanent redirect, so links and search engines move to the new URL.
  if (profile.id !== id) permanentRedirect(`/politicians/${encodeURIComponent(profile.id)}`);
  const [breakdown, peers, charts] = await Promise.all([
    fetchBreakdown(profile.branch, id),
    fetchPeers(profile.branch, id),
    fetchCharts(profile.branch, id),
  ]);

  return (
    <>
      <JsonLd
        data={[
          personJsonLd(id, profile),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            ...recordTrail(profile.branch).crumbs.map((c) => ({
              name: c.label,
              url: absoluteUrl(c.href),
            })),
            {
              name: profile.identity.name,
              url: absoluteUrl(`/politicians/${encodeURIComponent(id)}`),
            },
          ]),
        ]}
      />
      <PoliticianProfileClient
        profile={profile}
        breakdown={breakdown}
        peers={peers}
        charts={charts}
        strip={<ThisWeekStrip variant="compact" />}
        supportLine={
          profile.branch === "senate" || profile.branch === "house" ? (
            <PresidentialSupportLine memberId={profile.id} />
          ) : null
        }
      />
    </>
  );
}
