"use client";

import { Senator } from "@/types/senator";
import { calculateOverallScore, getScoreLabel, getScoreColor } from "@/lib/corruption";
import { useScoreWeights } from "@/hooks/useConfig";
import MetricTooltip from "./MetricTooltip";

interface RepresentationScoreProps {
  breakdown: Senator["representationScore"];
}

const SUB_SCORES: {
  key: keyof Senator["representationScore"];
  label: string;
  description: string;
}[] = [
  {
    key: "fundingIndependence",
    label: "Funding Independence",
    description: "How free is this senator from PAC and mega-donor influence? Penalizes heavy reliance on PAC money and concentration in a few top donors.",
  },
  {
    key: "promisePersistence",
    label: "Promise Persistence",
    description: "Are they keeping campaign promises? Compares stated platform commitments against actual votes, using AI analysis. Higher = more follow-through.",
  },
  {
    key: "independentVoting",
    label: "Independent Voting",
    description: "How often do they vote against their own party? Adjusted for state partisanship — breaking party line in a swing state counts less than in a deep-red/blue state.",
  },
  {
    key: "fundingDiversity",
    label: "Funding Diversity",
    description: "Is their funding spread across many industries, or dominated by a few? Uses Shannon entropy to measure concentration. Higher = more diverse funding sources.",
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
        <div className="w-48 shrink-0" id={`score-label-${label.replace(/\s+/g, "-").toLowerCase()}`}>
          <span className="text-matrix-green/70">{label}</span>
          <div className="text-[10px] text-matrix-green/50 leading-tight">{description}</div>
        </div>
        <span
          className="font-mono text-xs tracking-tight hidden sm:inline"
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-labelledby={`score-label-${label.replace(/\s+/g, "-").toLowerCase()}`}
        >
          <span className={colorClass} aria-hidden="true">{bar}</span>
        </span>
        <span className="sm:hidden flex-1">
          <span
            className="block h-2 bg-matrix-dark-green/30 border border-matrix-green/20"
            role="progressbar"
            aria-valuenow={value}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-labelledby={`score-label-${label.replace(/\s+/g, "-").toLowerCase()}`}
          >
            <span
              className={`block h-full ${
                value >= 70 ? "bg-matrix-green" : value >= 40 ? "bg-yellow-500" : "bg-red-500"
              }`}
              style={{ width: `${value}%` }}
            />
          </span>
        </span>
        <span className={`text-right w-12 shrink-0 font-pixel ${colorClass}`} aria-hidden="true">{value}</span>
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
          <div className="text-xs text-matrix-green/40">
            <MetricTooltip text="Weighted average of 4 sub-scores measuring how well this senator represents constituents. Based on funding sources, promise follow-through, voting independence, and funding diversity. 100 = ideal representation, 0 = none. Scores near 50 mean limited data.">
              REPRESENTATION SCORECARD
            </MetricTooltip>
          </div>
          <div className={`text-sm font-pixel ${colorClass} tracking-wider`}>{label}</div>
          <div className="text-[10px] text-matrix-green/50 mt-0.5">100 = fully represents constituents</div>
        </div>
      </div>

      <div className="space-y-3">
        {SUB_SCORES.map(({ key, label, description }) => (
          <ScoreBar key={key} value={breakdown[key]} label={label} description={description} />
        ))}
      </div>

      <div className="mt-3 text-[10px] text-matrix-green/50">
        Data: fec.gov · opensecrets.org · congress.gov · Scores regress toward 50 when data is sparse
      </div>
    </div>
  );
}
