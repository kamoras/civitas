import type { PresidentialContest as Contest } from "@/types/nextCycle";
import { candidateName, raisedLabel } from "@/lib/elections";
import { longDate } from "@/lib/specials";

/** The presidential contest, in the cycles that hold one: electoral votes by
 * state (the House apportionment plus two senators each; three for DC), the
 * candidates registered with the FEC, and the presidential primaries ahead.
 * Each state certifies who is on its own ballot, and no count is read here:
 * Civitas calls no race. */
export default function PresidentialContest({ data }: { data: Contest | null }) {
  if (!data?.presidential) return null;
  const { electoralVotes: ev } = data;
  const candidates = data.candidates ?? [];
  return (
    <section
      id="president"
      aria-labelledby="president-heading"
      className="mt-6 border font-sans border-phos/20 bg-surface px-4 pb-4"
    >
      <h2
        id="president-heading"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs text-ink-min"
      >
        President · {data.cycle}
      </h2>
      {ev.total > 0 ? (
        <p className="mt-3 font-display text-2xl font-semibold leading-tight text-ink-hi">
          {ev.total} electoral votes · {ev.toWin} to win
        </p>
      ) : (
        <p className="mt-3 text-sm text-ink-lo">
          The House apportionment the electoral votes come from couldn&apos;t be read just now.
        </p>
      )}
      <p className="mt-2 text-sm text-ink-lo">
        Each state casts one electoral vote per senator and representative, so its House seats plus
        two; the District of Columbia casts three. A majority of all electors elects. Civitas reads
        no presidential count and calls no race: each state&apos;s election office publishes its
        own.
      </p>
      {ev.states.length > 0 && (
        <details className="mt-2">
          <summary className="flex min-h-11 cursor-pointer items-center font-mono text-xs text-ink-min">
            Electoral votes by state
          </summary>
          <ul className="mt-1 grid grid-cols-3 gap-px bg-white/[0.07] sm:grid-cols-6 lg:grid-cols-9">
            {ev.states.map((s) => (
              <li
                key={s.state}
                className="flex items-baseline justify-between bg-surface px-2 py-1.5"
                aria-label={`${s.stateName}: ${s.votes} electoral votes`}
              >
                <span className="font-mono text-sm text-ink-hi" aria-hidden="true">
                  {s.state}
                </span>
                <span className="font-mono text-xs tabular-nums text-ink-lo" aria-hidden="true">
                  {s.votes}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <h3 className="mt-4 font-mono text-xs text-ink-min">Registered with the FEC</h3>
      {candidates.length ? (
        <>
          <p className="mt-1 text-sm text-ink-lo">
            {data.registered} {data.registered === 1 ? "candidate has" : "candidates have"} raised
            or spent past the FEC&apos;s threshold; the {candidates.length} who have raised the most
            are listed. None is on a ballot until a state certifies it.
          </p>
          <ul className="mt-1">
            {candidates.map((c) => (
              <li
                key={c.id}
                className="flex flex-wrap justify-between gap-x-3 border-t border-white/[0.07] py-1.5 text-sm"
              >
                <span className="text-ink-hi">
                  {candidateName(c)}{" "}
                  <span className="font-mono text-xs text-ink-min">{c.party}</span>
                </span>
                <span className="font-mono text-xs text-ink-lo">{raisedLabel(c)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-1 text-sm text-ink-lo">
          No presidential candidate has passed the FEC&apos;s threshold for {data.cycle} yet.
        </p>
      )}
      {(data.primaries ?? []).length > 0 && (
        <>
          <h3 className="mt-4 font-mono text-xs text-ink-min">
            Presidential primaries and caucuses ahead
          </h3>
          <ul className="mt-1">
            {data.primaries!.map((p) => (
              <li
                key={`${p.date}-${p.state}-${p.label}`}
                className="border-t border-white/[0.07] py-1.5 text-sm text-ink-lo"
              >
                <span className="font-mono text-[11px] text-ink-min">
                  {longDate(p.date).toUpperCase()}
                </span>{" "}
                {p.stateName}: {p.label}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
