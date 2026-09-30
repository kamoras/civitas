import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { LiveResults, StateBallot } from "@/types/election";
import { fetchRecord } from "@/lib/ssrPayload";
import { stateBallotHref } from "@/lib/elections";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeStateBallot } from "@/lib/seo";
import { showsResults } from "@/lib/results";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import StateBallotClient from "./StateBallotClient";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Short revalidate, matching the client-side TTL: measures are certified
// and struck by courts continuously through a cycle, so this is not the
// "reference data" tier its shape might suggest.
const REVALIDATE_S = 120;

/** Null only for a state with no ballot page; an outage throws (fetchRecord). */
function fetchStateBallot(state: string): Promise<StateBallot | null> {
  return fetchRecord<StateBallot>(
    `${BACKEND}/api/elections/states/${encodeURIComponent(state)}`,
    { next: { revalidate: REVALIDATE_S } },
    "state",
    "senateRaces"
  );
}

/** Whether Civitas reads `state`'s count live, from the results endpoint's
 * own list; null when that couldn't be checked. Asked only in the results
 * window, when the page leads with the count. */
async function fetchIsLive(state: string): Promise<boolean | null> {
  try {
    const res = await fetch(`${BACKEND}/api/elections/results?state=${encodeURIComponent(state)}`, {
      next: { revalidate: REVALIDATE_S },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<LiveResults>;
    return Array.isArray(body?.liveStates) ? body.liveStates.includes(state) : null;
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
  const path = stateBallotHref(code);

  if (!ballot) {
    return pageMetadata({
      title: `${code} ballot`,
      description: `Federal contests and statewide ballot measures for ${code}.`,
      path,
      noindex: true,
    });
  }

  // From election day the page leads with the count (its h1 becomes
  // "<State> results" once polls close), so the title and description say
  // so — from the same phase the page renders by.
  const resultsMode = showsResults(ballot.phase);
  const live = resultsMode ? await fetchIsLive(code) : null;
  const { title, description } = describeStateBallot(ballot, resultsMode, live);
  const ogImage = absoluteUrl(`/api/og?state=${code}`);
  return pageMetadata({
    title,
    description,
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
          {
            name: ballot.stateName ?? ballot.state,
            url: absoluteUrl(stateBallotHref(ballot.state)),
          },
        ])}
      />
      {/* Keyed by state: a soft navigation to another state is a new page
          with its own #race- arrival, never the last one's latch. */}
      <StateBallotClient key={ballot.state} ballot={ballot} />
    </>
  );
}
