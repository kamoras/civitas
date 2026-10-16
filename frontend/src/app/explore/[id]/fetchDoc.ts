import { fetchRecord } from "@/lib/ssrPayload";
import type { ExploreDocumentDetail } from "@/lib/api";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** A document's address: its permanent key ("fr-2026-17123", backend
 * services/explore_keys.py) or, from links made before keys, its row id. */
export const EXPLORE_REF_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/;

/** The document, by key or row id, for the page and its metadata (one
 * fetch, shared through Next's fetch cache). Null only when there is no
 * such document; an outage throws, so app/error.tsx says the page could
 * not be loaded and a crawler retries (fetchRecord). */
export async function fetchExploreDoc(ref: string): Promise<ExploreDocumentDetail | null> {
  if (!EXPLORE_REF_RE.test(ref)) return null;
  return fetchRecord<ExploreDocumentDetail>(
    `${BACKEND}/api/explore/${encodeURIComponent(ref)}`,
    { next: { revalidate: 3600 } },
    "id",
    "title"
  );
}
