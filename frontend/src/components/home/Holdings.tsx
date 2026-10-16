import Link from "next/link";
import { connection } from "next/server";
import type { SourceRow } from "@/types/sources";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/sources.py).
const REVALIDATE_S = 300;

/**
 * The provenance panel: what this record is built from, how much each
 * source has supplied and how recent it is, and under what terms.
 *
 * The source rows are a ledger from GET /api/sources, counted from the
 * stored records: a researcher can see at a glance that the votes run to
 * the last day the chambers sat, or that the Court's last decision is from
 * June. It used to be a fixed list of source names; when the backend can't
 * answer, that list is what shows.
 *
 * Laid out as a form: label column, value column, hairline between rows.
 */

const FALLBACK_SOURCES: readonly { label: string; detail: string }[] = [
  { label: "MEMBERS", detail: "Congress.gov · Senate.gov · Clerk.House.gov" },
  { label: "MONEY", detail: "FEC: itemised receipts and committee filings" },
  { label: "BILLS", detail: "Congress.gov stage histories · GovInfo full text" },
  { label: "COURT", detail: "Oyez: argued cases and opinion alignment" },
  { label: "ECONOMY", detail: "BLS · BEA · Federal Register" },
];

const TERMS: readonly { label: string; detail: string }[] = [
  { label: "CODE", detail: "Open source · AGPL-3.0 · re-runnable" },
  { label: "MODELS", detail: "Run locally, no third-party AI services" },
  { label: "SCORES", detail: "Fixed formulas, no AI · missing data scores a neutral 50" },
];

const ROW =
  "grid grid-cols-[minmax(0,5.5rem)_minmax(0,1fr)] gap-2 [overflow-wrap:anywhere] border-b border-white/[0.07] py-2";
const DT = "font-mono text-xs tracking-[0.08em] text-ink-min";
const DD = "font-mono text-xs leading-relaxed text-ink-lo";

async function fetchSources(): Promise<SourceRow[] | null> {
  // Per request, never prerendered: `next build` can't reach the backend.
  await connection();
  try {
    const res = await fetch(`${BACKEND}/api/sources`, { next: { revalidate: REVALIDATE_S } });
    if (!res.ok) return null;
    return ((await res.json()) as { sources: SourceRow[] }).sources;
  } catch {
    return null;
  }
}

export default async function Holdings() {
  const sources = await fetchSources();
  return (
    <aside>
      <h2 className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        <span>Built from the public record</span>
        <Link
          href="/about/data#sources"
          className="normal-case tracking-[0.06em] underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          Every source &rarr;
        </Link>
      </h2>

      <dl className="mt-1">
        {sources
          ? sources.map((s) => (
              <div key={s.label} className={ROW}>
                <dt className={DT}>{s.label}</dt>
                <dd className={DD}>
                  <span className="block text-ink">{s.source}</span>
                  <span className="block">{s.coverage}</span>
                  <span className="block text-ink-min">
                    {s.latest ? `${s.dateLabel} ${s.latest}` : "nothing on record yet"}
                  </span>
                </dd>
              </div>
            ))
          : FALLBACK_SOURCES.map(({ label, detail }) => (
              <div key={label} className={ROW}>
                <dt className={DT}>{label}</dt>
                <dd className={DD}>{detail}</dd>
              </div>
            ))}
        {TERMS.map(({ label, detail }) => (
          <div key={label} className={ROW}>
            <dt className={DT}>{label}</dt>
            <dd className={DD}>{detail}</dd>
          </div>
        ))}
      </dl>

      <p className="mt-4 font-display text-base leading-relaxed text-ink-lo">
        Civitas is an open source, volunteer-run project, not a registered organization of any kind.
        It takes no money from parties, candidates or PACs, requires no account, and sells nothing.
      </p>

      <Link
        href="/about/scores"
        className="mt-3 inline-block border-b border-phos-mid/40 font-mono text-xs tracking-[0.1em] text-phos-mid hover:text-phos"
      >
        HOW SCORES ARE COMPUTED →
      </Link>
    </aside>
  );
}
