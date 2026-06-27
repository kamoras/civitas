"use client";

import { Senator, CampaignPromise, VotingRecord, SponsoredBill } from "@/types/senator";
import { calculateOverallScore, getScoreLabel, getScoreColor } from "@/lib/corruption";
import { useScoreWeights } from "@/hooks/useConfig";
import MetricTooltip from "./MetricTooltip";
import { TECHNICAL_TERMS } from "@/lib/plainLanguage";
import type { ScoreKey } from "@/lib/plainLanguage";

interface RepresentationScoreProps {
  breakdown: Senator["representationScore"];
  promises?: CampaignPromise[];
  votingRecord?: VotingRecord;
  funding?: Senator["funding"];
  sponsoredBills?: SponsoredBill[];
  rank?: number;
  totalInChamber?: number;
}

function getScoreGrade(score: number): string {
  if (score >= 80) return "A";
  if (score >= 60) return "B";
  if (score >= 40) return "C";
  if (score >= 20) return "D";
  return "F";
}

const SCORE_KEYS: ScoreKey[] = [
  "fundingIndependence",
  "promisePersistence",
  "independentVoting",
  "fundingDiversity",
  "legislativeEffectiveness",
];

const METRIC_BLURBS: Record<ScoreKey, string> = {
  fundingIndependence: "How little of their campaign comes from PACs",
  promisePersistence: "Do their votes match their campaign promises?",
  independentVoting: "How often they break from their party",
  fundingDiversity: "How many different industries fund them",
  legislativeEffectiveness: "How well they advance bills they sponsor",
};

function ScoreBar({
  value,
  label,
  blurb,
  basis,
}: {
  value: number;
  label: string;
  blurb: string;
  basis?: string;
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
          <div className="text-[10px] text-neon-cyan/50 leading-tight">{blurb}</div>
          {basis && <div className="text-[10px] text-matrix-green/40 italic leading-tight">{basis}</div>}
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

export default function CorruptionScore({ breakdown, promises, votingRecord, funding, sponsoredBills, rank, totalInChamber }: RepresentationScoreProps) {
  const weights = useScoreWeights();
  const overall = calculateOverallScore(breakdown, weights);
  const label = getScoreLabel(overall);
  const colorClass = getScoreColor(overall);
  const grade = getScoreGrade(overall);

  const evaluable = (promises ?? []).filter(p => p.alignment !== "unclear").length;
  const totalPromises = (promises ?? []).length;
  const promiseBasis: string | undefined =
    totalPromises === 0
      ? "no platform data · defaults to 50"
      : evaluable === 0
        ? `${totalPromises} promise${totalPromises !== 1 ? "s" : ""}, none evaluable · defaults to 50`
        : `${evaluable} of ${totalPromises} had enough evidence to score`;

  const votingBasis: string | undefined =
    !votingRecord || votingRecord.totalVotes === 0
      ? "no voting record · defaults to 50"
      : `${votingRecord.totalVotes} votes tracked`;

  const fundingBasis: string | undefined =
    !funding || funding.totalRaised === 0
      ? "no funding data · defaults to 50"
      : undefined;

  const nBills = sponsoredBills?.length ?? 0;
  const effectivenessBasis: string =
    nBills === 0
      ? "no bill data · defaults to 50"
      : nBills < 10
        ? `${nBills} bill${nBills !== 1 ? "s" : ""} sponsored · score shrunk toward 50`
        : `${nBills} bills sponsored`;

  const scoreBasis: Partial<Record<ScoreKey, string | undefined>> = {
    promisePersistence: promiseBasis,
    independentVoting: votingBasis,
    fundingIndependence: fundingBasis,
    fundingDiversity: fundingBasis,
    legislativeEffectiveness: effectivenessBasis,
  };

  return (
    <div>
      <div className="flex items-end gap-4 mb-1">
        <div className="flex items-baseline gap-2">
          <div className={`text-5xl sm:text-6xl font-pixel ${colorClass}`}>{overall}</div>
          <div className={`text-2xl sm:text-3xl font-pixel ${colorClass} opacity-70`} title={`Grade: ${grade}`}>{grade}</div>
        </div>
        <div className="pb-2">
          <div className="text-xs text-matrix-green/40">
            <MetricTooltip text="Weighted average of 5 sub-scores measuring how well this senator represents constituents. Based on funding sources, promise follow-through, voting independence, funding diversity, and legislative effectiveness. 100 = ideal representation, 0 = none. Scores near 50 mean limited data.">
              REPRESENTATION SCORECARD
            </MetricTooltip>
          </div>
          <div className={`text-sm font-pixel ${colorClass} tracking-wider`}>{label}</div>
          <div className="text-[10px] text-matrix-green/50 mt-0.5">100 = fully represents constituents</div>
        </div>
      </div>

      {rank != null && totalInChamber != null && (
        <div className="mb-4 text-xs font-mono tracking-wide">
          <span className="text-neon-cyan/80">RANKS #{rank} OF {totalInChamber}</span>
          <span className="text-matrix-green/30 mx-2">·</span>
          <span className="text-matrix-green/60">BETTER THAN {Math.round(((totalInChamber - rank) / totalInChamber) * 100)}% OF THE CHAMBER</span>
        </div>
      )}

      <div className="space-y-3 mt-4">
        {SCORE_KEYS.map((key) => {
          const t = TECHNICAL_TERMS[key];
          return (
            <ScoreBar
              key={key}
              value={breakdown[key]}
              label={t.label}
              blurb={METRIC_BLURBS[key]}
              basis={scoreBasis[key]}
            />
          );
        })}
      </div>

      <div className="mt-3 text-[10px] text-matrix-green/50">
        Data: fec.gov · opensecrets.org · congress.gov · Scores regress toward 50 when data is sparse
      </div>
    </div>
  );
}
