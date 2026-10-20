import type { President } from "@/types/president";
import { fetchRecord } from "@/lib/ssrPayload";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The roster changes at an inauguration; the scores nightly.
const REVALIDATE_S = 300;

/** The sitting president, or null when none is on record (the API's 404,
 *  as during a transition before the roster is read); an outage throws. */
export function fetchSittingPresident(): Promise<President | null> {
  return fetchRecord<President>(
    `${BACKEND}/api/presidents/current`,
    { next: { revalidate: REVALIDATE_S } },
    "id",
    "name"
  );
}

/** Every president, newest first (so the sitting one leads), each with the
 *  historians' rank the backend computes. An outage throws (app/error.tsx),
 *  never an empty list. */
export async function fetchPresidents(): Promise<President[]> {
  const res = await fetch(`${BACKEND}/api/presidents`, { next: { revalidate: REVALIDATE_S } });
  if (!res.ok) throw new Error(`/api/presidents: HTTP ${res.status}`);
  const body: unknown = await res.json();
  if (!Array.isArray(body)) throw new Error("/api/presidents: not a list");
  return body as President[];
}
