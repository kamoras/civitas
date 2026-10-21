import { courtFinding, JUSTICE_RESEARCH_URL } from "@/lib/justices";
import { INLINE_LINK } from "@/lib/controlStyles";
import type { AppointerFinding } from "@/types/court";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/court.py).
const REVALIDATE_S = 300;

async function fetchFinding(): Promise<AppointerFinding | null> {
  try {
    const res = await fetch(`${BACKEND}/api/court/finding`, { next: { revalidate: REVALIDATE_S } });
    if (!res.ok) return null;
    return (await res.json()) as AppointerFinding | null;
  } catch {
    return null;
  }
}

/**
 * The Court-level finding on votes under the appointing president, from the
 * backend (GET /api/court/finding, the generated app/data/court_finding.json).
 * When the backend can't answer (at build time, or an outage), points to the
 * research note instead of stating a figure.
 */
export default async function CourtFinding() {
  const sentence = courtFinding(await fetchFinding());
  if (sentence) return <>{sentence}</>;
  return (
    <>
      The Court-level estimate is in{" "}
      <a href={JUSTICE_RESEARCH_URL} className={INLINE_LINK}>
        the research note
      </a>
      .
    </>
  );
}
