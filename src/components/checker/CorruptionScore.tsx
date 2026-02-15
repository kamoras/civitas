"use client";

import { Senator } from "@/types/senator";
import { calculateOverallScore, getScoreLabel, getScoreColor } from "@/lib/corruption";

interface CorruptionScoreProps {
  breakdown: Senator["corruptionScore"];
}

const SUB_SCORES: {
  key: keyof Senator["corruptionScore"];
  label: string;
}[] = [
  { key: "corporateFunding", label: "Corporate Funding" },
  { key: "lobbyistAlignment", label: "Lobbyist Alignment" },
  { key: "industryConcentration", label: "Industry Concentration" },
  { key: "flipFlopIndex", label: "Flip-Flop Index" },
  { key: "revolvingDoor", label: "Revolving Door" },
];

function ScoreBar({ value, label }: { value: number; label: string }) {
  const filled = Math.round(value / 5);
  const empty = 20 - filled;
  const bar = "█".repeat(filled) + "░".repeat(empty);

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 text-sm">
      <span className="text-matrix-green/60 w-48 shrink-0">{label}</span>
      <span className="font-mono text-xs tracking-tight hidden sm:inline">
        <span
          className={
            value >= 70 ? "text-red-500" : value >= 40 ? "text-yellow-500" : "text-matrix-green"
          }
        >
          {bar}
        </span>
      </span>
      <span className="sm:hidden flex-1">
        <span className="block h-2 bg-matrix-dark-green/30 border border-matrix-green/20">
          <span
            className={`block h-full ${
              value >= 70 ? "bg-red-500" : value >= 40 ? "bg-yellow-500" : "bg-matrix-green"
            }`}
            style={{ width: `${value}%` }}
          />
        </span>
      </span>
      <span className="text-matrix-green/80 w-10 text-right">{value}</span>
    </div>
  );
}

export default function CorruptionScore({ breakdown }: CorruptionScoreProps) {
  const overall = calculateOverallScore(breakdown);
  const label = getScoreLabel(overall);
  const colorClass = getScoreColor(overall);

  return (
    <div>
      <div className="flex items-end gap-4 mb-4">
        <div className={`text-5xl sm:text-6xl font-pixel ${colorClass}`}>{overall}</div>
        <div className="pb-2">
          <div className="text-xs text-matrix-green/40">CORPORATE INFLUENCE INDEX</div>
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
          This index is a weighted composite of five publicly available metrics: Corporate Funding
          (30%), Lobbyist Alignment (25%), Industry Concentration (20%), Flip-Flop Index (15%), and
          Revolving Door (10%). Higher scores indicate greater measurable corporate influence — not
          necessarily wrongdoing.
        </p>
        <p>
          Sources: FEC campaign finance filings (fec.gov), OpenSecrets.org industry &amp; donor
          data, Senate Lobbying Disclosure Act filings (lda.senate.gov), GovTrack.us voting records.
        </p>
        <p className="italic">
          Note: Correlation between donations and votes does not prove causation. Many factors
          influence legislative decisions.
        </p>
      </div>
    </div>
  );
}
