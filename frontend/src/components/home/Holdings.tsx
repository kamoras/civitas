import Link from "next/link";

/**
 * The provenance panel: what this record is built from, and under what terms.
 *
 * Deliberately static and fetch-free. An earlier draft put live coverage
 * counts here, which duplicated numbers the records band and /leaderboard
 * already carry and cost four extra requests on the homepage to do it. What
 * a visitor cannot get anywhere else is the standing statement — which
 * federal sources, whose code, what licence — so that is what this holds.
 *
 * Laid out as a form: label column, value column, hairline between rows.
 */

const SOURCES: readonly { label: string; detail: string }[] = [
  { label: "MEMBERS", detail: "Congress.gov · Senate.gov · Clerk.House.gov" },
  { label: "MONEY", detail: "FEC — itemised receipts and committee filings" },
  { label: "BILLS", detail: "GovInfo — full text and stage histories" },
  { label: "COURT", detail: "Oyez — argued cases and opinion alignment" },
  { label: "ECONOMY", detail: "BLS · BEA · Federal Register" },
];

const TERMS: readonly { label: string; detail: string }[] = [
  { label: "CODE", detail: "Open source · AGPL-3.0 · re-runnable" },
  { label: "MODELS", detail: "Run locally — no third-party AI services" },
  { label: "SCORES", detail: "Deterministic — no estimates, no imputation" },
];

export default function Holdings() {
  return (
    <aside className="md:col-span-4">
      <h2 className="border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        Built from the public record
      </h2>

      <dl className="mt-1">
        {SOURCES.map(({ label, detail }) => (
          <div
            key={label}
            className="grid grid-cols-[5.5rem_1fr] gap-2 border-b border-white/[0.07] py-2"
          >
            <dt className="font-mono text-xs tracking-[0.08em] text-ink-min">{label}</dt>
            <dd className="font-mono text-xs leading-relaxed text-ink-lo">{detail}</dd>
          </div>
        ))}
        {TERMS.map(({ label, detail }) => (
          <div
            key={label}
            className="grid grid-cols-[5.5rem_1fr] gap-2 border-b border-white/[0.07] py-2"
          >
            <dt className="font-mono text-xs tracking-[0.08em] text-ink-min">{label}</dt>
            <dd className="font-mono text-xs leading-relaxed text-ink-lo">{detail}</dd>
          </div>
        ))}
      </dl>

      <p className="mt-4 font-display text-base leading-relaxed text-ink-lo">
        Civitas is a non-profit, volunteer-run project. It takes no money from parties, candidates
        or PACs, requires no account, and sells nothing.
      </p>

      <Link
        href="/about"
        className="mt-3 inline-block border-b border-phos-mid/40 font-mono text-xs tracking-[0.1em] text-phos-mid hover:text-phos"
      >
        HOW SCORES ARE COMPUTED →
      </Link>
    </aside>
  );
}
