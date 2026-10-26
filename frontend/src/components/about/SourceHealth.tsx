import { P, Sub } from "@/components/about/AboutPage";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import type { SourceHealth as Health, SourceHealthEntry } from "@/types/sources";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/sources.py).
const REVALIDATE_S = 300;

async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${BACKEND}/api/sources/health`, {
      next: { revalidate: REVALIDATE_S },
    });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

const FAILING: SourceHealthEntry["state"][] = ["walled", "refused", "unavailable"];

/** The source map's view: a box per source that can't be read now, then
 *  every source in a table. Every word and date is the backend's. */
export function SourceHealthView({ health }: { health: Health }) {
  const failing = health.sources.filter((s) => FAILING.includes(s.state));
  return (
    <>
      <P>{health.summary}</P>
      {failing.length > 0 && (
        <BoxGrid columns={2}>
          {failing.map((s) => (
            <RecordBox
              key={s.host}
              level={4}
              compact
              kicker={s.stateLabel}
              tone="deadline"
              title={<span className="[overflow-wrap:anywhere]">{s.host}</span>}
              dek={s.line}
              more={
                s.reads.length
                  ? {
                      label: "what we read there",
                      children: (
                        <ul className="space-y-1">
                          {s.reads.map((r) => (
                            <li key={r}>{r}</li>
                          ))}
                        </ul>
                      ),
                    }
                  : null
              }
            />
          ))}
        </BoxGrid>
      )}
      <details className="group">
        <summary className="min-h-6 cursor-pointer text-body text-signal-cyan underline underline-offset-2">
          Every source ({health.sources.length.toLocaleString("en-US")})
        </summary>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-small">
            <caption className="sr-only">
              Every source Civitas reads, and whether it answered
            </caption>
            <thead>
              <tr className="text-ink-lo">
                <th scope="col" className="py-1 pr-3 font-normal">
                  Source
                </th>
                <th scope="col" className="py-1 pr-3 font-normal">
                  State
                </th>
                <th scope="col" className="py-1 font-normal">
                  Last good read
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.07] align-top">
              {health.sources.map((s) => (
                <tr key={s.host}>
                  <th scope="row" className="py-2 pr-3 font-normal">
                    <span className="block text-ink [overflow-wrap:anywhere]">{s.host}</span>
                    {s.reads.map((r) => (
                      <span key={r} className="block text-ink-lo">
                        {r}
                      </span>
                    ))}
                  </th>
                  <td className="py-2 pr-3">
                    <span className="block text-ink">{s.stateLabel}</span>
                    {s.state !== "ok" && <span className="block text-ink-lo">{s.line}</span>}
                  </td>
                  <td className="py-2 tabular-nums">{s.lastGood ?? "none on record"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

/**
 * Whether Civitas can read each of its sources now (GET /api/sources/health):
 * every host the code reads, its state when last asked, its last good read
 * and, when it can't be read, since when and why. A source not asked in its
 * latest run reads as that, never as a failure. When the backend can't
 * answer, says so.
 */
export default async function SourceHealth() {
  const health = await fetchHealth();
  return (
    <Sub title="Can each source be read now" id="source-health">
      {health ? (
        <SourceHealthView health={health} />
      ) : (
        <P>Whether each source can be read can&apos;t be checked right now.</P>
      )}
    </Sub>
  );
}
