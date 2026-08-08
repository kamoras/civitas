import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { StateBallot } from "@/types/election";
import StateBallotClient from "./StateBallotClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

// Short revalidate, matching the client-side TTL: measures are certified
// and struck by courts continuously through a cycle, so this is not the
// "reference data" tier its shape might suggest.
const REVALIDATE_S = 120;

async function fetchBallot(state: string): Promise<StateBallot | null> {
  try {
    const res = await fetch(
      `${BACKEND}/api/elections/states/${encodeURIComponent(state)}/ballot`,
      { next: { revalidate: REVALIDATE_S } },
    );
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// Per-state metadata, not inherited from the elections layout — otherwise
// all 50 state pages would ship the same title and a canonical OG url
// pointing at /elections.
export async function generateMetadata({
  params,
}: {
  params: Promise<{ state: string }>;
}): Promise<Metadata> {
  const { state } = await params;
  const code = state.toUpperCase();
  const ballot = await fetchBallot(code);

  const title = ballot
    ? `${code} ballot ${ballot.cycleYear} — federal contests and statewide measures — Civitas`
    : `${code} ballot — Civitas`;
  const description = ballot
    ? `What ${code} votes on ${ballot.electionDate}: U.S. Senate and House contests and ${ballot.measures.length} statewide ballot ${ballot.measures.length === 1 ? "measure" : "measures"}, quoted from official sources.`
    : `Federal contests and statewide ballot measures for ${code}.`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/elections/states/${code}`,
      siteName: "Civitas",
    },
  };
}

export default async function StateBallotPage({
  params,
}: {
  params: Promise<{ state: string }>;
}) {
  const { state } = await params;
  const ballot = await fetchBallot(state.toUpperCase());

  if (!ballot) notFound();

  return <StateBallotClient ballot={ballot} />;
}
