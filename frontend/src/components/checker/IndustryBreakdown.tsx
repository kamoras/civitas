"use client";

import { useState } from "react";
import { IndustryDonation, Donor } from "@/types/senator";
import { useIndustries } from "@/hooks/useConfig";
import { formatCurrency } from "@/lib/formatting";

interface IndustryBreakdownProps {
  industries: IndustryDonation[];
  donors: Donor[];
}

export default function IndustryBreakdown({ industries, donors }: IndustryBreakdownProps) {
  const [expandedIndustry, setExpandedIndustry] = useState<string | null>(null);
  const industryMap = useIndustries();
  const sorted = [...industries].sort((a, b) => b.total - a.total);
  const maxTotal = sorted[0]?.total || 1;

  const getDonorsForIndustry = (industryCode: string): Donor[] => {
    return donors.filter((d) => d.industry === industryCode).sort((a, b) => b.total - a.total);
  };

  return (
    <div>
      <div className="text-xs text-ink-lo mb-3">
        Where this senator&apos;s money comes from, classified by industry using AI embedding
        similarity. Click an industry to see individual donors.
      </div>
      <div className="space-y-2">
        {sorted.map((ind) => {
          const info = industryMap[ind.industry];
          const displayName = info?.name || ind.name || ind.industry.replace(/_/g, " ");
          const color = info?.color || "#444444";
          const barWidth = Math.round((ind.total / maxTotal) * 100);
          const isExpanded = expandedIndustry === ind.industry;
          const industryDonors = getDonorsForIndustry(ind.industry);
          const hasDonerBreakdown = industryDonors.length > 0;

          return (
            <div key={ind.industry} className="text-sm">
              {hasDonerBreakdown ? (
                <button
                  className="flex justify-between mb-1 w-full text-left cursor-pointer hover:text-phos transition-colors"
                  onClick={() => setExpandedIndustry(isExpanded ? null : ind.industry)}
                  aria-expanded={isExpanded}
                  aria-label={`${displayName}: ${formatCurrency(ind.total)} (${ind.percentage}%). ${isExpanded ? "Collapse" : "Expand"} donor list`}
                >
                  <span className="text-ink flex items-center gap-1">
                    <span className="text-xs text-ink-min" aria-hidden="true">
                      {isExpanded ? "−" : "+"}
                    </span>
                    {displayName}
                  </span>
                  <span className="text-ink-lo">
                    {formatCurrency(ind.total)} ({ind.percentage}%)
                  </span>
                </button>
              ) : (
                <div className="flex justify-between mb-1">
                  <span className="text-ink">{displayName}</span>
                  <span className="text-ink-lo">
                    {formatCurrency(ind.total)} ({ind.percentage}%)
                  </span>
                </div>
              )}
              <div className="h-3 bg-white/[0.03] border border-white/[0.07] mb-2">
                <div
                  className="h-full transition-all duration-500"
                  style={{
                    width: `${barWidth}%`,
                    backgroundColor: color,
                    boxShadow: `0 0 8px ${color}40`,
                  }}
                />
              </div>

              {isExpanded && industryDonors.length > 0 && (
                <div className="ml-4 mb-3 space-y-1 border-l-2 border-white/[0.07] pl-3">
                  {industryDonors.map((donor) => (
                    <div key={donor.name} className="flex justify-between text-xs">
                      <span className="text-ink-lo">{donor.name}</span>
                      <span className="text-signal-cyan">{formatCurrency(donor.total)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
