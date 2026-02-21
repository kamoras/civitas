"use client";

import { Senator } from "@/types/senator";
import { calculateOverallScore, getScoreLabel, getScoreColor } from "@/lib/corruption";
import { useScoreWeights } from "@/hooks/useConfig";

interface RepresentationScoreProps {
  breakdown: Senator["representationScore"];
}

const SUB_SCORES: {
  key: keyof Senator["representationScore"];
  label: string;
  description: string;
}[] = [
  {
    key: "constituentFunding",
    label: "Constituent Funding",
    description: "Small donors vs. PAC money",
  },
  {
    key: "independenceIndex",
    label: "Independence Index",
    description: "Votes free from donor/lobbyist influence",
  },
  {
    key: "promiseFulfillment",
    label: "Promise Fulfillment",
    description: "Campaign commitments kept vs. broken",
  },
  {
    key: "accountability",
    label: "Accountability",
    description: "Attendance, transparency, and engagement",
  },
  {
    key: "donorDiversity",
    label: "Donor Diversity",
    description: "Funding spread across industries",
  },
];

function ScoreBar({
  value,
  label,
  description,
}: {
  value: number;
  label: string;
  description: string;
}) {
  const filled = Math.round(value / 5);
  const empty = 20 - filled;
  const bar = "█".repeat(filled) + "░".repeat(empty);

  const colorClass =
    value >= 70 ? "text-matrix-green" : value >= 40 ? "text-yellow-500" : "text-red-500";

  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 text-sm">
        <div className="w-48 shrink-0">
          <span className="text-matrix-green/70">{label}</span>
          <div className="text-[10px] text-matrix-green/35 leading-tight">{description}</div>
        </div>
        <span className="font-mono text-xs tracking-tight hidden sm:inline">
          <span className={colorClass}>{bar}</span>
        </span>
        <span className="sm:hidden flex-1">
          <span className="block h-2 bg-matrix-dark-green/30 border border-matrix-green/20">
            <span
              className={`block h-full ${
                value >= 70 ? "bg-matrix-green" : value >= 40 ? "bg-yellow-500" : "bg-red-500"
              }`}
              style={{ width: `${value}%` }}
            />
          </span>
        </span>
        <span className={`text-right w-12 shrink-0 font-pixel ${colorClass}`}>{value}</span>
      </div>
    </div>
  );
}

export default function CorruptionScore({ breakdown }: RepresentationScoreProps) {
  const weights = useScoreWeights();
  const overall = calculateOverallScore(breakdown, weights);
  const label = getScoreLabel(overall);
  const colorClass = getScoreColor(overall);

  return (
    <div>
      <div className="flex items-end gap-4 mb-4">
        <div className={`text-5xl sm:text-6xl font-pixel ${colorClass}`}>{overall}</div>
        <div className="pb-2">
          <div className="text-xs text-matrix-green/40">REPRESENTATION SCORECARD</div>
          <div className={`text-sm font-pixel ${colorClass} tracking-wider`}>{label}</div>
          <div className="text-[10px] text-matrix-green/30 mt-0.5">100 = fully represents constituents</div>
        </div>
      </div>

      <div className="space-y-3">
        {SUB_SCORES.map(({ key, label, description }) => (
          <ScoreBar key={key} value={breakdown[key]} label={label} description={description} />
        ))}
      </div>

      <div className="mt-3 text-[10px] text-matrix-green/25">
        Data: fec.gov · opensecrets.org · congress.gov
      </div>
    </div>
  );
}
