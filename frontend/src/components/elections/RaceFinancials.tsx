import { formatCurrency } from "@/lib/formatting";
import type { CandidateSummary } from "@/types/election";

/**
 * Side-by-side cash-on-hand bars, scaled to whichever candidate in this
 * race has the most cash on hand. Bar width is a display-only scaling of
 * real backend-provided numbers, not a new derived metric. Callers pass
 * only "active" candidates (see lib/elections.ts isActiveCandidate) so
 * paper filers don't pad the chart.
 */
export default function RaceFinancials({ candidates }: { candidates: CandidateSummary[] }) {
  // A negative cash on hand (FEC debt exceeding receipts — see
  // CandidateCard) breaks this chart two ways: it would set `max` too
  // low if the debt is deep, and (cashOnHand / max) * 100 goes negative,
  // which CSS silently clamps to a 0-width bar with no explanation. This
  // chart is about comparing who has runway, not who owes money, so a
  // debt candidate is simply not part of the comparison here — their
  // figure still shows on their own CandidateCard, labeled as debt.
  const withFunds = candidates.filter((c) => c.cashOnHand != null && c.cashOnHand >= 0);
  if (withFunds.length === 0) {
    return (
      <p className="font-mono text-base text-ink-min">
        No fundraising data synced for this race yet.
      </p>
    );
  }

  const max = Math.max(...withFunds.map((c) => c.cashOnHand as number));

  return (
    <div>
      <h3 className="mb-3 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
        Cash on hand
      </h3>
      <ol className="space-y-3">
        {withFunds
          .slice()
          .sort((a, b) => (b.cashOnHand as number) - (a.cashOnHand as number))
          .map((c) => (
            <li key={c.id}>
              <div className="mb-1 flex items-baseline justify-between gap-4">
                <span className="font-display text-[15px] text-ink">{c.name}</span>
                <span className="font-mono text-base tabular-nums text-ink-hi">
                  {formatCurrency(c.cashOnHand as number)}
                </span>
              </div>
              {/* Square bar on a square track: the register has no 
                  corners, and this is the same bar language the scorecard and
                  leaderboard use. */}
              <div className="h-2 w-full bg-white/10">
                <div
                  className="h-full bg-phos-mid"
                  style={{ width: `${max > 0 ? ((c.cashOnHand as number) / max) * 100 : 0}%` }}
                />
              </div>
            </li>
          ))}
      </ol>
    </div>
  );
}
