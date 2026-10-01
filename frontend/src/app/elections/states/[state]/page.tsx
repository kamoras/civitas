import { Metadata } from "next";
import { notFound } from "next/navigation";
import type { LiveResults, StateBallot } from "@/types/election";
import { fetchRecord } from "@/lib/ssrPayload";
import { stateBallotHref } from "@/lib/elections";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeStateBallot } from "@/lib/seo";
import { pollsClosed, showsResults } from "@/lib/results";
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

/** From the results endpoint: whether Civitas reads `state`'s count live
 * (its own list; null when that couldn't be checked), and whether the
 * state's last polls are known to have closed (pollsClosed — the rule the
 * page's own heading turns on; unknown is "not closed"). Asked only in the
 * results window. */
async function fetchLiveStatus(
  state: string,
  phase: StateBallot["phase"],
  now: number
): Promise<{ live: boolean | null; closed: boolean }> {
  // The day after election day every state's polls have closed.
  const resultsDay = phase?.phase === "results";
  try {
    const res = await fetch(`${BACKEND}/api/elections/results?state=${encodeURIComponent(state)}`, {
      next: { revalidate: REVALIDATE_S },
    });
    if (!res.ok) return { live: null, closed: resultsDay };
    const body = (await res.json()) as Partial<LiveResults>;
    const live = Array.isArray(body?.liveStates) ? body.liveStates.includes(state) : null;
    const closed =
      resultsDay ||
      (!!body?.phase &&
        showsResults(body.phase) &&
        pollsClosed(
          {
            phase: body.phase,
            pollsClose: body.pollsClose,
            feeds: body.feeds,
            races: Array.isArray(body.races) ? body.races : [],
          },
          state,
          now
        ));
    return { live, closed };
  } catch {
    return { live: null, closed: resultsDay };
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

  // From election day the page leads with the count, but its h1 becomes
  // "<State> results" only once the state's polls close; until then people
  // are voting and the page is still ballot research. The title and
  // description turn at the same moment, by the same rule — a card shared
  // that morning must not read "Election Results".
  const status = showsResults(ballot.phase)
    ? await fetchLiveStatus(code, ballot.phase, Date.now())
    : null;
  const { title, description } = describeStateBallot(
    ballot,
    !!status?.closed,
    status?.live ?? null
  );
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
