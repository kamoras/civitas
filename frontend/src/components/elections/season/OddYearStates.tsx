import type { OddYearStates as Odd } from "@/types/nextCycle";
import { safeHref } from "@/lib/formatting";

/** The states holding regular state elections in the coming odd year (by
 * the constitution or law Civitas's scope file cites), and plainly what
 * Civitas doesn't cover of them: no candidates and no counts, only the
 * state's own election office. */
export default function OddYearStates({ data }: { data: Odd | null }) {
  if (!data) return null;
  return (
    <section
      aria-labelledby="odd-year"
      className="mt-6 border font-sans border-phos/20 bg-surface px-4 pb-4"
    >
      <h2
        id="odd-year"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        State elections in {data.year}
      </h2>
      {data.states.length ? (
        <>
          <p className="mt-3 text-sm text-ink-lo">
            {data.states.length} {data.states.length === 1 ? "state holds" : "states hold"} regular
            state elections in {data.year}, by their own constitutions and laws. Civitas
            doesn&apos;t list these contests&apos; candidates or read their counts: its state
            readers cover each federal cycle&apos;s November general. Each state&apos;s election
            office has its ballot and dates. Spring and local elections (city, county, school board)
            aren&apos;t listed here.
          </p>
          <ul className="mt-2">
            {data.states.map((s) => {
              const url = safeHref(s.office.url);
              return (
                <li key={s.state} className="border-t border-white/[0.07] py-2 text-sm text-ink-lo">
                  <span className="text-ink-hi">{s.stateName}</span> votes on{" "}
                  {new Intl.ListFormat("en-US").format(s.words)}.{" "}
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex min-h-6 items-center underline decoration-white/30 hover:text-phos"
                    >
                      {s.stateName}&apos;s election office ↗
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <p className="mt-3 text-sm text-ink-lo">
          No state holds a regular November state election in {data.year} by the record Civitas
          keeps; every state votes in the federal cycle&apos;s even years.
        </p>
      )}
    </section>
  );
}
