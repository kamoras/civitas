import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { StateBallot } from "@/types/election";
import { usableRecord } from "@/lib/ssrPayload";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import StateBallotClient from "./StateBallotClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

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
// all 50 state pages would ship the same title and a canonical pointing at
// /elections. Leads with the state's full name: "California ballot 2026"
// is the search; "CA ballot" is not.
export async function generateMetadata({
  params,
}: {
  params: Promise<{ state: string }>;
}): Promise<Metadata> {
  const { state } = await params;
  const code = state.toUpperCase();
  const ballot = await fetchStateBallot(code);
  // Always the upper-case code: /elections/states/ca serves the same page.
  const path = `/elections/states/${code}`;

  if (!ballot) {
    return pageMetadata({
      title: `${code} ballot`,
      description: `Federal contests and statewide ballot measures for ${code}.`,
      path,
      noindex: true,
    });
  }

  const name = ballot.stateName ?? code;
  const n = ballot.measures.length;
  const title = `${name} Ballot ${ballot.cycleYear}: Senate, House Races & Ballot Measures`;
  const ogImage = absoluteUrl(`/api/og?state=${code}`);
  return pageMetadata({
    title,
    description: `What's on the ${ballot.cycleYear} ${name} ballot (${ballot.electionDate}): U.S. Senate and House candidates with FEC fundraising${n > 0 ? `, and ${n} statewide ballot ${n === 1 ? "measure" : "measures"} quoted from official sources` : ""}.`,
    path,
    type: "article",
    images: [{ url: ogImage, width: 1200, height: 630, alt: title }],
  });
}

export default async function StateBallotPage({ params }: { params: Promise<{ state: string }> }) {
  const { state } = await params;
  const ballot = await fetchStateBallot(state.toUpperCase());

  if (!ballot) notFound();

  return (
    <>
      <JsonLd
        data={breadcrumbList([
          { name: "Home", url: absoluteUrl("/") },
          { name: "Elections", url: absoluteUrl("/elections") },
          { name: ballot.stateName ?? ballot.state, url: absoluteUrl(`/elections/states/${ballot.state}`) },
        ])}
      />
      <StateBallotClient ballot={ballot} />
    </>
  );
}
