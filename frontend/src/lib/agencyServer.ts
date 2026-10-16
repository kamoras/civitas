import type { AgencyDetail, AgencyIndex } from "@/types/agency";
import { fetchRecord } from "@/lib/ssrPayload";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The agency list and budgets change weekly; documents nightly.
const REVALIDATE_S = 600;

/** The directory. An outage throws (app/error.tsx), never an empty list. */
export async function fetchAgencyIndex(): Promise<AgencyIndex> {
  const index = await fetchRecord<AgencyIndex>(
    `${BACKEND}/api/agencies`,
    { next: { revalidate: REVALIDATE_S } },
    "agencies"
  );
  if (!index) throw new Error("/api/agencies: no directory");
  return index;
}

/** One agency, or null when there is no such agency (a 404); an outage
 * throws (fetchRecord). */
export async function fetchAgency(slug: string): Promise<AgencyDetail | null> {
  if (!/^[a-z0-9-]{1,200}$/.test(slug)) return null;
  return fetchRecord<AgencyDetail>(
    `${BACKEND}/api/agencies/${slug}`,
    { next: { revalidate: REVALIDATE_S } },
    "slug",
    "name"
  );
}
