"use client";

import { Senator } from "@/types/senator";
import { calculateOverallScore, getScoreLabel, getScoreColor } from "@/lib/corruption";

interface RepresentationScoreProps {
  breakdown: Senator["representationScore"];
}

const SUB_SCORES: {
  key: keyof Senator["representationScore"];
  label: string;
}[] = [
  { key: "constituentFunding", label: "Constituent Funding" },
  { key: "independenceIndex", label: "Independence Index" },
  { key: "donorDiversity", label: "Donor Diversity" },
  { key: "promiseFulfillment", label: "Promise Fulfillment" },
  { key: "accountability", label: "Accountability" },
];

function ScoreBar({ value, label }: { value: number; label: string }) {
  const filled = Math.round(value / 5);
  const empty = 20 - filled;
  const bar = "█".repeat(filled) + "░".repeat(empty);

  // Higher = green (good), lower = red (bad)
  const colorClass =
    value >= 70 ? "text-matrix-green" : value >= 40 ? "text-yellow-500" : "text-red-500";

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 text-sm">
      <span className="text-matrix-green/60 w-48 shrink-0">{label}</span>
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
      <span className="text-matrix-green/80 w-10 text-right">{value}</span>
    </div>
  );
}

export default function CorruptionScore({ breakdown }: RepresentationScoreProps) {
  const overall = calculateOverallScore(breakdown);
  const label = getScoreLabel(overall);
  const colorClass = getScoreColor(overall);

  return (
    <div>
      <div className="flex items-end gap-4 mb-4">
        <div className={`text-5xl sm:text-6xl font-pixel ${colorClass}`}>{overall}</div>
        <div className="pb-2">
          <div className="text-xs text-matrix-green/40">REPRESENTATION SCORECARD</div>
          <div className={`text-sm font-pixel ${colorClass} tracking-wider`}>{label}</div>
        </div>
      </div>

      <div className="space-y-2">
        {SUB_SCORES.map(({ key, label }) => (
          <ScoreBar key={key} value={breakdown[key]} label={label} />
        ))}
      </div>
      <div className="mt-4 p-3 border border-matrix-green/10 bg-matrix-dark-green/10 text-[10px] text-matrix-green/40 space-y-1">
        <div className="text-matrix-green/50 font-bold mb-1">METHODOLOGY</div>
        <p>
          Higher score = better constituent representation. Weighted composite of five metrics:
          Constituent Funding (30%), Promise Fulfillment (30%), Independence Index (20%),
          Donor Diversity (10%), Accountability (10%).
        </p>
        <p>
          <strong>Constituent Funding</strong>: small donor % and inverse PAC ratio.{" "}
          <strong>Promise Fulfillment</strong>: platform-to-vote alignment (currently proxied by
          party loyalty; full promise analysis coming soon).{" "}
          <strong>Independence Index</strong>: inverse of lobbying alignment rate.{" "}
          <strong>Donor Diversity</strong>: inverse Herfindahl-Hirschman Index of industry donors.{" "}
          <strong>Accountability</strong>: inverse institutional capture heuristic.
        </p>
        <p>
          Sources: FEC campaign finance filings (fec.gov), OpenSecrets.org industry &amp; donor
          data, Senate Lobbying Disclosure Act filings (lda.senate.gov), GovTrack.us voting records.
        </p>
        <p className="italic">
          Note: Higher scores indicate stronger measurable alignment with constituent interests —
          not necessarily virtue. Many factors influence legislative decisions.
          Pro-business platform senators who deliver may legitimately score lower on some metrics.
        </p>
      </div>
    </div>
  );
}
