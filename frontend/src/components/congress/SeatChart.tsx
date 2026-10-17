import type { PartySplit, VoteMember } from "@/types/congress";
import { PARTY_PLURAL as PARTY_NAME } from "@/lib/partyStyles";

// An election of the Speaker's "candidate" positions are drawn by the
// party bars instead (RollCallView).
type Bucket = Exclude<VoteMember["bucket"], "candidate">;

/** Each position's mark: told apart by shape as well as colour (WCAG
 * 1.4.1), every mark at 3:1 or more on the surface (1.4.11). */
const MARK: Record<Bucket, { label: string; dot: string }> = {
  yea: { label: "Yea", dot: "rounded-full bg-phos-mid" },
  nay: { label: "Nay", dot: "rounded-full border-2 border-signal-red" },
  present: { label: "Present", dot: "bg-ink-lo" },
  notVoting: { label: "Not voting", dot: "rounded-full border border-dashed border-ink-min" },
};
const ORDER: Bucket[] = ["yea", "nay", "present", "notVoting"];

function Mark({ bucket }: { bucket: Bucket }) {
  return <span className={`inline-block h-2.5 w-2.5 shrink-0 ${MARK[bucket].dot}`} />;
}

/**
 * The chamber as dots: one per member, grouped by party, marked by how they
 * voted, so "decided by one vote" can be seen at a glance. The parties'
 * counts are the API's (`parties`, counted from the same positions); the
 * table under the dots is the chart's text equivalent and what a screen
 * reader reads. Inside the vote's share section, so a shared image of the
 * vote carries it.
 */
export default function SeatChart({
  members,
  parties,
  chamberName,
}: {
  members: VoteMember[];
  parties: PartySplit[];
  chamberName: string;
}) {
  const byParty = new Map<string, VoteMember[]>();
  for (const m of members)
    if (m.bucket !== "candidate")
      byParty.set(m.party || "?", [...(byParty.get(m.party || "?") ?? []), m]);
  const shown = ORDER.filter((b) => parties.some((p) => p[b] > 0));
  return (
    <figure className="flex min-w-0 flex-col gap-3">
      <figcaption className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
        The {chamberName}, one dot per member
      </figcaption>
      <div aria-hidden="true" className="flex flex-col gap-2.5">
        {parties.map((p) => {
          const group = (byParty.get(p.party) ?? [])
            .slice()
            .sort((a, b) => ORDER.indexOf(a.bucket as Bucket) - ORDER.indexOf(b.bucket as Bucket));
          return (
            <div key={p.party} className="flex flex-col gap-1">
              <span className="text-sm text-ink">{PARTY_NAME[p.party] ?? p.party}</span>
              <div data-seats={p.party} className="flex flex-wrap gap-[3px]">
                {group.map((m, i) => (
                  <Mark key={i} bucket={m.bucket as Bucket} />
                ))}
              </div>
            </div>
          );
        })}
        <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-ink-lo">
          {shown.map((b) => (
            <span key={b} className="inline-flex items-center gap-1.5">
              <Mark bucket={b} />
              {MARK[b].label}
            </span>
          ))}
        </div>
      </div>
      {/* At a large text size the table scrolls in its own box (reachable by
          keyboard) rather than widening the page. */}
      <div role="region" aria-label="How each party voted" tabIndex={0} className="overflow-x-auto">
        <table className="w-full max-w-md text-left font-mono text-xs tabular-nums">
          <caption className="sr-only">How each party voted</caption>
          <thead>
            <tr className="text-ink-min">
              <th scope="col" className="py-1 pr-3 font-normal uppercase tracking-[0.1em]">
                Party
              </th>
              {shown.map((b) => (
                <th
                  key={b}
                  scope="col"
                  className="py-1 pr-3 text-right font-normal uppercase tracking-[0.1em]"
                >
                  {MARK[b].label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {parties.map((p) => (
              <tr key={p.party} className="border-t border-white/[0.07] text-ink-lo">
                <th scope="row" className="py-1 pr-3 font-sans text-sm font-normal text-ink">
                  {PARTY_NAME[p.party] ?? p.party}
                </th>
                {shown.map((b) => (
                  <td key={b} className="py-1 pr-3 text-right">
                    {p[b]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
