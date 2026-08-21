import { notFound, redirect } from "next/navigation";

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

  let state: string | null = null;
  try {
    const res = await fetch(`${BACKEND}/api/elections/races/${encodeURIComponent(raceId)}`, {
      next: { revalidate: 120 },
    });
    if (res.ok) {
      const data = await res.json();
      state = typeof data?.state === "string" ? data.state : null;
    }
  } catch {
    state = null;
  }

  if (!state) notFound();

  redirect(`/elections/states/${state}#race-${raceId}`);
}
