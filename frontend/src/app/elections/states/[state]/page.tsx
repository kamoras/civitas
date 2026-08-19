import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { StateBallot } from "@/types/election";
import { usableRecord } from "@/lib/ssrPayload";
import StateBallotClient from "./StateBallotClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

async function fetchStateBallot(state: string): Promise<StateBallot | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/states/${encodeURIComponent(state)}`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    return usableRecord<StateBallot>(await res.json(), "state", "senateRaces");
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ state: string }>;
}): Promise<Metadata> {
  const { state } = await params;
  const ballot = await fetchStateBallot(state);

  const title = ballot ? `${ballot.state} Ballot — Civitas` : "State Ballot — Civitas";
  const description = ballot
    ? `Your ${ballot.cycleYear} U.S. Senate and House options in ${ballot.state} — every candidate on record.`
    : "State ballot detail on Civitas.";

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/elections/states/${state}`,
      siteName: "Civitas",
    },
  };
}

export default async function StateBallotPage({ params }: { params: Promise<{ state: string }> }) {
  const { state } = await params;
  const ballot = await fetchStateBallot(state);

  if (!ballot) notFound();

  return <StateBallotClient ballot={ballot} />;
}
