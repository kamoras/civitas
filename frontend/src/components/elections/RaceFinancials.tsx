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
  const withFunds = candidates.filter((c) => c.cashOnHand != null);
  if (withFunds.length === 0) {
    return (
      <p className="text-sm text-matrix-green/40">No fundraising data synced for this race yet.</p>
    );
  }

  const max = Math.max(...withFunds.map((c) => c.cashOnHand as number));

  return (
    <div>
      <h4 className="font-pixel text-[10px] text-matrix-green/40 tracking-widest mb-3">
        CASH ON HAND
      </h4>
      <div className="space-y-3">
        {withFunds
          .slice()
          .sort((a, b) => (b.cashOnHand as number) - (a.cashOnHand as number))
          .map((c) => (
            <div key={c.id}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-matrix-green/60">{c.name}</span>
                <span className="text-sm font-bold tabular-nums text-white/80">
                  {formatCurrency(c.cashOnHand as number)}
                </span>
              </div>
              <div className="w-full h-2 bg-white/10 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full bg-neon-cyan/60 transition-all duration-700"
                  style={{ width: `${max > 0 ? ((c.cashOnHand as number) / max) * 100 : 0}%` }}
                />
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}
