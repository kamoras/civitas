import Link from "next/link";
import type { BallotCandidate } from "@/types/election";
import { cashOnHandDisplay, formatCurrency } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";
import { getPartyMeta } from "@/components/elections/CandidateCard";

/**
 * A race as a comparison, not a list.
 *
 * Money raised is the most decision-relevant published fact this page
 * has about a candidate, and it rendered as a number in a stack of
 * cards — so telling a $1.9M campaign from a $130K one meant reading
 * and dividing. Measured on the live data, Georgia's House races run
 * 10:1 to 15:1 between the two candidates; that gap is the story and it
 * should be visible before it is read.
 *
 * Bars are scaled to the LEADER of this race, not to any national
 * figure: the question a voter is asking is "who is out-raising whom
 * here", and a scale that made a safe seat look empty next to a
 * battleground would answer a different one.
 *
 * Sorted by contributions descending. In a state still showing FEC
 * filers that ordering is doing real work — a 25-filer race puts the
 * people running actual campaigns first instead of leaving them in
 * whatever order the roster returned.
 */
export default function RaceMoneyBars({
  candidates,
  showUnconfirmed = false,
}: {
  candidates: BallotCandidate[];
  showUnconfirmed?: boolean;
}) {
  const ranked = [...candidates].sort(
    (a, b) => (b.contributions ?? 0) - (a.contributions ?? 0),
  );
  const leader = ranked[0]?.contributions ?? 0;

  return (
    <ul className="divide-y divide-ink-min/15">
      {ranked.map((c) => {
        const pm = getPartyMeta(c.party);
        const raised = c.contributions ?? 0;
        // Width is share-of-leader. A zero leader (nobody has reported)
        // must not divide — every bar is simply empty, which is the
        // truthful picture of a race where no money exists yet.
        const pct = leader > 0 ? Math.round((raised / leader) * 100) : 0;
        const incumbent = c.incumbentChallenge === "I";
        const cash = cashOnHandDisplay(c.cashOnHand);

        return (
          <li key={c.id} className="py-3">
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 font-display text-sm text-ink-hi">
                <span className={`font-mono text-xs ${pm.color}`}>{pm.label}</span>
                <span className="mx-1.5 text-ink-min">·</span>
                <span className="break-words">{c.name}</span>
                {incumbent && (
                  <span className="ml-2 bg-ink-hi/90 px-1.5 py-0.5 font-mono text-[10px] text-surface-base">
                    INCUMBENT
                  </span>
                )}
                {showUnconfirmed && !c.confirmed && (
                  <span className="ml-2 font-mono text-[10px] text-ink-min">UNCONFIRMED</span>
                )}
              </p>
              <p className="shrink-0 font-mono text-xs tabular-nums text-ink-lo">
                {c.fecFiled === false
                  ? "no FEC filing"
                  : c.hasRaisedFunds
                    ? formatCurrency(raised)
                    : "no funds reported"}
              </p>
            </div>

            <div
              className="mt-2 h-1.5 w-full bg-ink-min/15"
              role="img"
              aria-label={
                leader > 0
                  ? `${formatCurrency(raised)} raised, ${pct}% of the leader in this race`
                  : "no fundraising reported in this race"
              }
            >
              <div className={`h-1.5 ${pm.rule}`} style={{ width: `${pct}%` }} />
            </div>

            {cash && (
              <p className="mt-1 font-mono text-[11px] text-ink-min">
                {cash.label.toLowerCase()} {cash.amount}
              </p>
            )}

            {c.incumbentRecord && (
              <Link
                href={`/politicians/${c.incumbentRecord.id}`}
                className="mt-1.5 inline-block font-mono text-[11px] text-ink-lo hover:text-phos"
              >
                representation score{" "}
                <span className={getScoreColor(c.incumbentRecord.score)}>
                  {c.incumbentRecord.score.toFixed(1)}
                </span>{" "}
                →
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );
}
