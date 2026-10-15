import { fetchRecord } from "@/lib/ssrPayload";
import type { ExploreDocumentDetail } from "@/lib/api";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** The document, for the page and its metadata (one fetch, shared through
 * Next's fetch cache). Null only when there is no such document; an outage
 * throws, so app/error.tsx says the page could not be loaded and a crawler
 * retries (fetchRecord). */
export async function fetchExploreDoc(id: string): Promise<ExploreDocumentDetail | null> {
  if (!/^\d+$/.test(id)) return null;
  return fetchRecord<ExploreDocumentDetail>(
    `${BACKEND}/api/explore/${id}`,
    { next: { revalidate: 3600 } },
    "id",
    "title"
  );
}
