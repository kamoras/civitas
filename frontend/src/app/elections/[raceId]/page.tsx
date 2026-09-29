import { notFound, redirect } from "next/navigation";
import { fetchRecord } from "@/lib/ssrPayload";
import { stateBallotHref } from "@/lib/elections";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** 2026-08 revamp: race detail merged into the state ballot page (no more
 * "top level page + nested full race page" maze) — this route now only
 * exists so old links (Bluesky posts already published under
 * /elections/{raceId}) keep working, by redirecting to the race's
 * section of its state page. */
export default async function RaceDetailRedirect({
  params,
}: {
  params: Promise<{ raceId: string }>;
}) {
  const { raceId } = await params;

  // Null only for a race that doesn't exist; an outage throws (fetchRecord),
  // so a published link isn't answered with a 404 while the backend is down.
  const race = await fetchRecord<{ state: string }>(
    `${BACKEND}/api/elections/races/${encodeURIComponent(raceId)}`,
    { next: { revalidate: 120 } },
    "state"
  );
  const state = typeof race?.state === "string" ? race.state : null;

  if (!state) notFound();

  redirect(`${stateBallotHref(state)}#race-${raceId}`);
}
