import type { BallotCandidate, RaceWithCandidates } from "@/types/election";
import { isActiveCandidate, tierCandidates } from "@/lib/elections";
import { formatCurrency } from "@/lib/formatting";
import { getPartyMeta } from "@/components/elections/CandidateCard";

/** What the money column says for one candidate. Never a fabricated $0:
 * someone the state lists who never filed with the FEC, and someone the
 * FEC has not synced yet, each say so in words. */
function raisedLabel(c: BallotCandidate): string {
  if (c.fecFiled === false) return "no FEC filing";
  if (c.lastFinancialsSync == null) return "awaiting FEC sync";
  if (!c.hasRaisedFunds || c.contributions == null) return "no funds reported";
  return `${formatCurrency(c.contributions)} raised`;
}

/** A federal race's candidates as ballot rows: name, party in words and
 * colour, money raised, and a bar scaled to this race's top fundraiser
 * (the same share-of-leader scale RaceMoneyBars uses).
 *
 * A race whose list the state has not confirmed ("filers"/"primary") shows
 * only its leaders here, with the rest counted — the full field is one
 * click away in the drawer, and a 25-filer race laid flat in a ballot
 * column is exactly the endless list this page exists to avoid. */
export default function BallotRaceRows({ race }: { race: RaceWithCandidates }) {
  const active = race.candidates.filter(isActiveCandidate);
  const tiered = race.candidateSource === "filers" || race.candidateSource === "primary";
  const { leaders, tail } = tiered ? tierCandidates(active) : { leaders: active, tail: [] };
  const rows = [...leaders].sort((a, b) => (b.contributions ?? 0) - (a.contributions ?? 0));
  const leader = rows[0]?.contributions ?? 0;

  if (active.length === 0) {
    return <p className="px-4 py-3 text-[13px] text-ink-lo">No candidates on record for this race yet.</p>;
  }

  return (
    <ul>
      {rows.map((c) => {
        const party = getPartyMeta(c.party);
        const pct = leader > 0 ? Math.round(((c.contributions ?? 0) / leader) * 100) : 0;
        return (
          <li key={c.id} className="border-b border-white/[0.09] px-4 py-2.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="flex min-w-0 flex-col">
                <span className="break-words text-[15px] font-bold text-ink-hi">
                  {c.name}
                  {c.incumbentChallenge === "I" && (
                    <span className="ml-2 align-middle font-mono text-[10px] font-normal tracking-[0.08em] text-ink-lo">
                      INCUMBENT
                    </span>
                  )}
                </span>
                <span className={`font-mono text-[11px] tracking-[0.08em] ${party.color}`}>{party.label}</span>
              </span>
              <span className="shrink-0 font-mono text-xs tabular-nums text-ink-lo">{raisedLabel(c)}</span>
            </div>
            <div className="mt-1.5 h-1 bg-white/[0.07]" aria-hidden="true">
              <div className={`h-1 ${party.rule}`} style={{ width: `${pct}%` }} />
            </div>
          </li>
        );
      })}
      {tail.length > 0 && (
        <li className="border-b border-white/[0.09] px-4 py-2 font-mono text-xs text-ink-lo">
          + {tail.length} more filed with the FEC
        </li>
      )}
    </ul>
  );
}
