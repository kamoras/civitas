import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { StateBallot } from "@/types/election";
import { usableRecord } from "@/lib/ssrPayload";
import StateBallotClient from "./StateBallotClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

// Short revalidate, matching the client-side TTL: measures are certified
// and struck by courts continuously through a cycle, so this is not the
// "reference data" tier its shape might suggest.
const REVALIDATE_S = 120;

async function fetchStateBallot(state: string): Promise<StateBallot | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/states/${encodeURIComponent(state)}`, {
      next: { revalidate: REVALIDATE_S },
    });
    if (!res.ok) return null;
    return usableRecord<StateBallot>(await res.json(), "state", "senateRaces");
  } catch {
    return null;
  }
}

// Per-state metadata, not inherited from the elections layout — otherwise
// all 50 state pages would ship the same title and a canonical OG url
// pointing at /elections.
export async function generateMetadata({
  params,
  searchParams,
}: {
  params: Promise<{ state: string }>;
  searchParams: Promise<{ race?: string }>;
}): Promise<Metadata> {
  const { state } = await params;
  const code = state.toUpperCase();
  const ballot = await fetchStateBallot(code);

  const title = ballot
    ? `${code} ballot ${ballot.cycleYear} — federal contests and statewide measures — Civitas`
    : `${code} ballot — Civitas`;
  const description = ballot
    ? `What ${code} votes on ${ballot.electionDate}: U.S. Senate and House contests and ${ballot.measures.length} statewide ballot ${ballot.measures.length === 1 ? "measure" : "measures"}, quoted from official sources.`
    : `Federal contests and statewide ballot measures for ${code}.`;

  // A link to one specific race (e.g. a Bluesky coverage post) carries
  // ?race=<id> — the #race-<id> fragment it also sets never reaches the
  // server, so it can't drive which OG card renders. /api/og itself
  // re-validates the id against the fetched ballot and falls back to the
  // generic state card on any mismatch, so an unrecognized id here is
  // harmless: it's passed through, not validated twice.
  const { race } = await searchParams;
  const ogImage = race
    ? `${SITE}/api/og?state=${code}&race=${encodeURIComponent(race)}`
    : `${SITE}/api/og?state=${code}`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/elections/states/${code}`,
      siteName: "Civitas",
      images: [{ url: ogImage, width: 1200, height: 630, alt: title }],
      type: "article",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [{ url: ogImage, alt: title }],
    },
  };
}

export default async function StateBallotPage({ params }: { params: Promise<{ state: string }> }) {
  const { state } = await params;
  const ballot = await fetchStateBallot(state.toUpperCase());

  if (!ballot) notFound();

  return <StateBallotClient ballot={ballot} />;
}
