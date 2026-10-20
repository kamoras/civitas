import type { ElectionAdministration as Admin } from "@/types/nextCycle";
import { safeHref } from "@/lib/formatting";

type RateKey = keyof Admin["national"];

const RATES: { key: RateKey; label: string }[] = [
  { key: "turnout", label: "of registered voters voted" },
  { key: "byMail", label: "of voters voted by mail" },
  { key: "mailRejected", label: "of mail ballots returned were rejected" },
  { key: "provisionalRejected", label: "of provisional ballots were rejected" },
];

const pct = (x: number | null) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);

/** How the last federal general was run, from the EAC's Election
 * Administration and Voting Survey: national rates over the states whose
 * every jurisdiction reported, and each state's in a table. A state that
 * reported in part has no rate, said with a dash and how many reported. */
export default function ElectionAdministration({ data }: { data: Admin | null }) {
  if (!data) return null;
  const href = safeHref(data.url);
  return (
    <section
      aria-labelledby="election-administration"
      className="mt-6 border font-sans border-phos/20 bg-surface px-4 pb-4"
    >
      <h2
        id="election-administration"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs text-ink-min"
      >
        How the {data.year} election was run
      </h2>
      <dl className="mt-3 grid grid-cols-1 gap-px bg-white/[0.07] sm:grid-cols-2 lg:grid-cols-4">
        {RATES.map(({ key, label }) => (
          <div key={key} className="bg-surface px-3 py-2.5">
            <dt className="sr-only">{label}</dt>
            <dd className="font-display text-2xl font-semibold leading-none tabular-nums text-ink-hi">
              {pct(data.national[key].rate)}
            </dd>
            <dd className="mt-1 text-sm leading-snug text-ink-lo">
              {label}{" "}
              <span className="text-ink-min">
                ({data.national[key].states} of {data.of} states fully reporting)
              </span>
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm text-ink-lo">
        From the U.S. Election Assistance Commission&apos;s Election Administration and Voting
        Survey, which every state and local election office answers after each federal general.
        Summed by state from each office&apos;s answers; a rate is shown only where every office in
        the state reported both counts.{" "}
        {href && (
          <a
            href={href}
            className="inline-flex min-h-6 items-center underline decoration-white/30 hover:text-phos"
          >
            The EAC&apos;s data (version {data.version})
          </a>
        )}
      </p>
      <details className="mt-2">
        <summary className="flex min-h-11 cursor-pointer items-center font-mono text-xs text-ink-min">
          Every state
        </summary>
        <div className="overflow-x-auto">
          <table className="mt-1 w-full min-w-[32rem] text-left text-sm">
            <caption className="sr-only">
              Election administration by state, {data.year}, from the EAC&apos;s survey
            </caption>
            <thead>
              <tr className="font-mono text-[11px] text-ink-min">
                <th scope="col" className="py-1.5 pr-3 font-normal">
                  State
                </th>
                <th scope="col" className="py-1.5 pr-3 text-right font-normal">
                  Turnout
                </th>
                <th scope="col" className="py-1.5 pr-3 text-right font-normal">
                  By mail
                </th>
                <th scope="col" className="py-1.5 pr-3 text-right font-normal">
                  Mail rejected
                </th>
                <th scope="col" className="py-1.5 text-right font-normal">
                  Provisional rejected
                </th>
              </tr>
            </thead>
            <tbody>
              {data.states.map((s) => (
                <tr key={s.state} className="border-t border-white/[0.07]">
                  <th scope="row" className="py-1.5 pr-3 font-normal text-ink-hi">
                    {s.stateName}
                  </th>
                  {(["turnout", "byMail", "mailRejected", "provisionalRejected"] as const).map(
                    (k) => (
                      <td
                        key={k}
                        className="py-1.5 pr-3 text-right font-mono tabular-nums text-ink-lo"
                      >
                        {pct(s.rates[k])}
                      </td>
                    )
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-ink-min">
          A dash: not every election office in the state reported both counts.
        </p>
      </details>
    </section>
  );
}
