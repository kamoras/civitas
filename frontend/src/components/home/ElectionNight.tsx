import Link from "next/link";
import { connection } from "next/server";
import type { ResultsSummary, ResultsSummaryState } from "@/types/election";
import { formatEasternTime } from "@/lib/results";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer in the results window
// (api/elections.py CACHE_TTL_RESULTS_S): the count moves on a five-minute sync.
const REVALIDATE_S = 30;

// States given in full; the rest are one tap away in the line below them,
// so any counting state is reachable from the first screen.
const SHOWN = 5;

const STATUS: Record<ResultsSummaryState["status"], { tag: string; tone: string }> = {
  not_final: { tag: "LEADING · NOT FINAL", tone: "text-signal-amber" },
  official: { tag: "LEADS · OFFICIAL COUNT", tone: "text-phos" },
};

const COMMAND =
  "inline-flex min-h-[32px] items-center font-mono text-xs tracking-[0.12em] text-phos-mid hover:text-phos";

/**
 * The live count, state by state, at the top of the homepage while the
 * results are up (backend election_phase: from election night until the
 * count settles). Outside that window, or before any state has counted,
 * it renders nothing and the homepage is unchanged.
 *
 * Every sentence is the backend's fixed template around the state's own
 * figures (live_results/summary.py): "leads", "not final" until the state
 * lists its count as official, never "wins". Civitas never calls a race.
 */
export default async function ElectionNight() {
  await connection();
  let data: ResultsSummary | null = null;
  try {
    const res = await fetch(`${BACKEND}/api/elections/results/summary`, {
      next: { revalidate: REVALIDATE_S },
    });
    if (res.ok) data = (await res.json()) as ResultsSummary;
  } catch {
    data = null;
  }
  if (!data || data.phase.phase === "campaign" || data.states.length === 0) return null;
  return <ElectionNightList data={data} />;
}

function Entry({ entry }: { entry: ResultsSummaryState }) {
  const status = STATUS[entry.status];
  return (
    <li className="grid grid-cols-1 gap-x-8 gap-y-1 border-b border-white/[0.07] py-3 md:grid-cols-[11rem_minmax(0,1fr)_auto]">
      <p className="font-mono text-xs leading-relaxed tracking-[0.08em]">
        <span className="text-ink-hi md:block">{entry.stateName.toUpperCase()}</span>
        <span className="md:hidden text-ink-min"> · </span>
        <span className={`md:block ${status.tone}`}>{status.tag}</span>
      </p>
      <div className="min-w-0">
        {entry.lines.map((line) => (
          <p key={line} className="font-display text-base leading-snug text-ink-hi">
            {line}
          </p>
        ))}
        <p className="mt-0.5 font-mono text-xs tracking-[0.06em] text-ink-min">
          {entry.source} · {formatEasternTime(entry.lastChangeAt)}
        </p>
      </div>
      <p>
        <Link href={entry.href} className={COMMAND}>
          [ {entry.stateName.toUpperCase()} RESULTS ]
        </Link>
      </p>
    </li>
  );
}

export function ElectionNightList({ data }: { data: ResultsSummary }) {
  const shown = data.states.slice(0, SHOWN);
  const more = data.states.slice(SHOWN);
  return (
    <section aria-labelledby="election-night" className="mx-auto mt-8 max-w-7xl px-4 sm:px-6">
      <h2
        id="election-night"
        className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        <span>The count · {data.states.length} states counting</span>
        <Link
          href="/elections"
          className="normal-case tracking-[0.06em] underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          Every state&apos;s count &rarr;
        </Link>
      </h2>
      <ul>
        {shown.map((e) => (
          <Entry key={e.state} entry={e} />
        ))}
      </ul>
      {more.length > 0 && (
        <p className="py-3 font-mono text-xs leading-loose tracking-[0.08em] text-ink-min">
          ALSO COUNTING:{" "}
          {more.map((e, i) => (
            <span key={e.state}>
              {i > 0 && " · "}
              <Link
                href={e.href}
                className="text-phos-mid underline decoration-white/30 underline-offset-4 hover:text-phos"
              >
                {e.stateName}
              </Link>
            </span>
          ))}
        </p>
      )}
      <p className="pb-1 text-sm leading-relaxed text-ink-lo">
        Counts as each state&apos;s election office publishes them. Civitas never calls a race:
        &ldquo;leads&rdquo; is the count so far, not a result.{" "}
        <Link href="/about/elections" className="underline underline-offset-2 hover:text-phos">
          How results are read
        </Link>
      </p>
    </section>
  );
}
