import Link from "next/link";
import type { NextField as Field } from "@/types/nextCycle";
import { STATE_NAMES } from "@/lib/stateCodes";
import { safeHref } from "@/lib/formatting";

const n = (x: number) => x.toLocaleString("en-US");
/** A count as a figure: "None" for zero, never a bare 0. */
const figure = (x: number) => (x === 0 ? "None" : n(x));

/** The next cycle's field as the FEC's filings stand: registrations, never
 * places on a ballot (a state certifies those months later). A sitting
 * member with no filing is said to have none: no official record states a
 * retirement, so the page never calls one. */
export default function NextField({ field }: { field: Field | null }) {
  return (
    <section
      aria-labelledby="next-field"
      className="mt-6 border font-sans border-phos/20 bg-surface px-4 pb-4"
    >
      <h2
        id="next-field"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Registered with the FEC{field ? ` for ${field.cycle}` : ""}
      </h2>
      {!field ? (
        <p className="mt-3 text-sm text-ink-lo">The FEC filings couldn&apos;t be read just now.</p>
      ) : (
        <>
          <p className="mt-3 font-display text-2xl font-semibold leading-tight text-ink-hi">
            {field.registered > 0
              ? `${n(field.registered)} ${field.registered === 1 ? "candidate has" : "candidates have"} registered for federal races`
              : "No candidate has registered for a Senate or House race yet"}
          </p>
          <p className="mt-2 text-sm text-ink-lo">
            Registering with the FEC is not a place on the ballot: each state certifies its own
            ballot months from now. Open seats are the FEC&apos;s own coding.
          </p>
          <dl className="mt-3 grid grid-cols-1 gap-px bg-white/[0.07] sm:grid-cols-3">
            <Stat
              figure={`${figure(field.house.filedForHouse)} of ${n(field.house.members)}`}
              label="sitting House members registered for the House"
            />
            <Stat
              figure={`${figure(field.senate.incumbentFiled)} of ${n(field.senate.seatsUp)}`}
              label="Senate seats up whose senator has registered"
            />
            <Stat figure={figure(field.openSeats)} label="races the FEC lists as open seats" />
          </dl>
          {field.filedForOtherOffice.length > 0 && (
            <>
              <h3 className="mt-4 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                Members registered for another office
              </h3>
              <ul className="mt-1">
                {field.filedForOtherOffice.map((m) => (
                  <li
                    key={m.id}
                    className="border-t border-white/[0.07] py-1.5 text-sm text-ink-lo"
                  >
                    <Link
                      href={`/politicians/${m.id}`}
                      className="inline-flex min-h-6 items-center text-ink-hi underline decoration-white/30 hover:text-phos"
                    >
                      {m.name}
                    </Link>{" "}
                    ({m.party}-{m.state}, {m.chamber === "S" ? "senator" : "representative"}), for{" "}
                    {m.office === "P"
                      ? "President"
                      : `${STATE_NAMES[m.state] ?? m.state}'s Senate seat`}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-3 text-sm text-ink-lo">
            {n(field.house.noFiling)} sitting House{" "}
            {field.house.noFiling === 1 ? "member has" : "members have"} no {field.cycle} filing on
            record yet
            {field.senate.noIncumbentFiling.length > 0 &&
              `, nor do the senators holding the seats up in ${field.senate.noIncumbentFiling.length} ${
                field.senate.noIncumbentFiling.length === 1 ? "state" : "states"
              }`}
            . No filing is not a retirement; Civitas reads no announcements.{" "}
            <a
              href={safeHref(field.sources.fec)}
              className="inline-flex min-h-6 items-center underline decoration-white/30 hover:text-phos"
            >
              The FEC&apos;s candidate list
            </a>
          </p>
        </>
      )}
    </section>
  );
}

function Stat({ figure, label }: { figure: string; label: string }) {
  return (
    <div className="bg-surface px-3 py-2.5">
      <dt className="sr-only">{label}</dt>
      <dd className="font-display text-2xl font-semibold leading-none tabular-nums text-ink-hi">
        {figure}
      </dd>
      <dd className="mt-1 text-sm leading-snug text-ink-lo" aria-hidden="true">
        {label}
      </dd>
    </div>
  );
}
