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
              title={s.publisher ?? s.host}
              dek={s.line}
              more={{
                label: "what we read there",
                children: (
                  <p>
                    {s.takes}. <span className="[overflow-wrap:anywhere]">{s.host}</span>
                  </p>
                ),
              }}
            />
          ))}
        </BoxGrid>
      )}
      <details className="group @container">
        <summary className="min-h-6 cursor-pointer text-body text-signal-cyan underline underline-offset-2">
          Every source ({health.sources.length.toLocaleString("en-US")})
        </summary>
        {/* A list, not a table: on a phone a three-column table left the
            state a word wide. Each row's line already names its last good
            read. */}
        <ul
          aria-label="Every source Civitas reads, and whether it answered"
          className="mt-3 divide-y divide-white/[0.07] text-small"
        >
          {health.sources.map((s) => (
            <li
              key={s.host}
              className="grid grid-cols-[minmax(0,1fr)] gap-x-4 gap-y-1 py-2 @min-[36rem]:grid-cols-2"
            >
              <div className="min-w-0">
                <span className="block text-ink">{s.publisher ?? s.host}</span>
                {s.takes && <span className="block text-ink-lo">{s.takes}</span>}
                <span className="block text-ink-min [overflow-wrap:anywhere]">{s.host}</span>
              </div>
              <div className="min-w-0">
                <span className="block text-ink">{s.stateLabel}</span>
                <span className="block text-ink-lo">{s.line}</span>
              </div>
            </li>
          ))}
        </ul>
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
