import { IndustryDonation } from "@/types/senator";
import { INDUSTRIES } from "@/data/industries";
import { formatCurrency } from "@/lib/formatting";

interface IndustryBreakdownProps {
  industries: IndustryDonation[];
}

export default function IndustryBreakdown({ industries }: IndustryBreakdownProps) {
  const sorted = [...industries].sort((a, b) => b.total - a.total);
  const maxTotal = sorted[0]?.total || 1;

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-lg text-neon-cyan neon-cyan">{">"} FOLLOW THE MONEY</h3>
        <span className="text-[10px] text-matrix-green/25">Source: opensecrets.org/industries</span>
      </div>
      <div className="space-y-2">
        {sorted.map((ind) => {
          const info = INDUSTRIES[ind.industry];
          const barWidth = Math.round((ind.total / maxTotal) * 100);
          return (
            <div key={ind.industry} className="text-sm">
              <div className="flex justify-between mb-1">
                <span className="text-matrix-green/70">{info?.name || ind.name}</span>
                <span className="text-matrix-green/50">
                  {formatCurrency(ind.total)} ({ind.percentage}%)
                </span>
              </div>
              <div className="h-3 bg-matrix-dark-green/30 border border-matrix-green/20">
                <div
                  className="h-full transition-all duration-500"
                  style={{
                    width: `${barWidth}%`,
                    backgroundColor: info?.color || "#00ff41",
                    boxShadow: `0 0 8px ${info?.color || "#00ff41"}40`,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
