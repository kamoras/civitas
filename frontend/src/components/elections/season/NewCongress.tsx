import type { ChamberCompared, NewCongress as Compared } from "@/types/nextCycle";
import { congressOrdinal } from "@/lib/sources";
import { safeHref } from "@/lib/formatting";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** How the sitting Congress compares with the one before, from each
 * member's Congress.gov record: who is new to each chamber, back after a
 * gap, or from the other chamber, and the median age by birth year. Those
 * are the official fields; no other demographic is in an official record,
 * so none is shown. */
export default function NewCongress({ data }: { data: Compared | null }) {
  return (
    <section
      aria-labelledby="new-congress"
      className="mt-6 border font-sans border-phos/20 bg-surface px-4 pb-4"
    >
      <h2
        id="new-congress"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs text-ink-min"
      >
        {data ? `The ${congressOrdinal(data.congress)} Congress, compared` : "The new Congress"}
      </h2>
      {!data ? (
        <p className="mt-3 text-sm text-ink-lo">
          The members&apos; records couldn&apos;t be read just now.
        </p>
      ) : (
        <>
          <div className="mt-3 grid grid-cols-1 gap-x-8 sm:grid-cols-2">
            <Chamber name="Senate" c={data.senate} />
            <Chamber name="House" c={data.house} />
          </div>
          <p className="mt-3 text-sm text-ink-lo">
            New means no earlier term in that chamber. Age is at January 3, {data.firstYear}, from
            birth year: Congress.gov gives the year only, so each member&apos;s age is this or one
            less. Gender, race and other traits aren&apos;t in the official record, so none is
            compared.{" "}
            <a
              href={safeHref(data.sources.congressGov)}
              className="inline-flex min-h-6 items-center underline decoration-white/30 hover:text-phos"
            >
              Congress.gov members
            </a>
          </p>
        </>
      )}
    </section>
  );
}

function Chamber({ name, c }: { name: string; c: ChamberCompared }) {
  return (
    <div className="border-t border-white/[0.07] py-2">
      <h3 className="font-mono text-xs text-ink-min">{name}</h3>
      {c.read === 0 ? (
        <p className="mt-1 text-sm text-ink-lo">
          No member&apos;s Congress.gov record lists this Congress yet ({c.members} sitting); the
          comparison appears as the records are updated.
        </p>
      ) : (
        <>
          <p className="mt-1 font-display text-2xl font-semibold leading-none tabular-nums text-ink-hi">
            {c.new} new{c.newShare != null ? ` · ${pct(c.newShare)}` : ""}
          </p>
          <p className="mt-1 text-sm text-ink-lo">
            {c.fromOtherChamber > 0 && `${c.fromOtherChamber} from the other chamber; `}
            {c.returning > 0 && `${c.returning} back after sitting out the last Congress; `}
            {c.medianAge != null
              ? `median age ${Math.round(c.medianAge)}`
              : "no birth years stored"}
            {c.read < c.members
              ? ` (records listing this Congress for ${c.read} of ${c.members} members)`
              : ""}
            .
          </p>
        </>
      )}
    </div>
  );
}
