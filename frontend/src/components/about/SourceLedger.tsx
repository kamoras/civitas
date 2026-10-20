import { Fact, Facts, P, Sub } from "@/components/about/AboutPage";
import type { SourceRow } from "@/types/sources";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/sources.py).
const REVALIDATE_S = 300;

async function fetchSources(): Promise<SourceRow[] | null> {
  try {
    const res = await fetch(`${BACKEND}/api/sources`, { next: { revalidate: REVALIDATE_S } });
    if (!res.ok) return null;
    return ((await res.json()) as { sources: SourceRow[] }).sources;
  } catch {
    return null;
  }
}

/**
 * What the record holds from each source now: how much it has supplied and
 * how recent its newest record is (GET /api/sources, counted from the stored
 * records). When the backend can't answer (at build time, or an outage),
 * says so; the sources themselves are described below it either way.
 */
export default async function SourceLedger() {
  const sources = await fetchSources();
  return (
    <Sub title="On record now" id="on-record">
      {sources ? (
        <Facts>
          {sources.map((s) => (
            <Fact key={s.label} label={s.label}>
              <span className="block text-ink">{s.source}</span>
              <span className="block">{s.coverage}</span>
              <span className="block text-ink-min">
                {s.latest ? `${s.dateLabel} ${s.latest}` : "nothing on record yet"}
              </span>
            </Fact>
          ))}
        </Facts>
      ) : (
        <P>The live counts can&apos;t be read right now. Every source is described below.</P>
      )}
    </Sub>
  );
}
