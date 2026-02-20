"use client";

import { useState } from "react";
import { IndustryDonation, Donor } from "@/types/senator";
import { INDUSTRIES } from "@/data/industries";
import { formatCurrency } from "@/lib/formatting";

interface IndustryBreakdownProps {
  industries: IndustryDonation[];
  donors: Donor[];
}

export default function IndustryBreakdown({ industries, donors }: IndustryBreakdownProps) {
  const [expandedIndustry, setExpandedIndustry] = useState<string | null>(null);
  const sorted = [...industries].sort((a, b) => b.total - a.total);
  const maxTotal = sorted[0]?.total || 1;

  // Helper to get donors for a specific industry
  const getDonorsForIndustry = (industryCode: string): Donor[] => {
    return donors
      .filter((d) => d.industry === industryCode)
      .sort((a, b) => b.total - a.total);
  };

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
          const isExpanded = expandedIndustry === ind.industry;
          const industryDonors = getDonorsForIndustry(ind.industry);
          const hasDonerBreakdown = industryDonors.length > 0;

          return (
            <div key={ind.industry} className="text-sm">
              <div
                className={`flex justify-between mb-1 ${hasDonerBreakdown ? "cursor-pointer hover:text-neon-cyan transition-colors" : ""}`}
                onClick={() => hasDonerBreakdown && setExpandedIndustry(isExpanded ? null : ind.industry)}
              >
                <span className="text-matrix-green/70 flex items-center gap-1">
                  {hasDonerBreakdown && (
                    <span className="text-[10px] text-matrix-green/40">
                      {isExpanded ? "[-]" : "[+]"}
                    </span>
                  )}
                  {info?.name || ind.name}
                </span>
                <span className="text-matrix-green/50">
                  {formatCurrency(ind.total)} ({ind.percentage}%)
                </span>
              </div>
              <div className="h-3 bg-matrix-dark-green/30 border border-matrix-green/20 mb-2">
                <div
                  className="h-full transition-all duration-500"
                  style={{
                    width: `${barWidth}%`,
                    backgroundColor: info?.color || "#00ff41",
                    boxShadow: `0 0 8px ${info?.color || "#00ff41"}40`,
                  }}
                />
              </div>

              {/* Expanded donor breakdown */}
              {isExpanded && industryDonors.length > 0 && (
                <div className="ml-4 mb-3 space-y-1 border-l-2 border-matrix-green/20 pl-3">
                  {industryDonors.map((donor) => (
                    <div key={donor.name} className="flex justify-between text-xs">
                      <span className="text-matrix-green/60">{donor.name}</span>
                      <span className="text-neon-cyan/70">{formatCurrency(donor.total)}</span>
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
