"use client";

import { Senator, VotingRecord, SponsoredBill } from "@/types/senator";
import { getScoreLabel, getScoreColor, getScoreBgColor, asciiScoreBar } from "@/lib/representation";
import MetricTooltip from "./MetricTooltip";
import ScoreBreakdownPanel from "@/components/shared/ScoreBreakdownPanel";
import { SCORE_TERMS } from "@/lib/scoreTerms";
import type { ScoreKey } from "@/lib/scoreTerms";

interface RepresentationScoreProps {
  breakdown: Senator["representationScore"];
  votingRecord?: VotingRecord;
  funding?: Senator["funding"];
  sponsoredBills?: SponsoredBill[];
  rank?: number;
  totalInChamber?: number;
  entityId?: string;
  chamber?: "senate" | "house";
}

// Letter-grade cutoffs are deliberately round numbers (80/60/40/20), one
// point below getScoreColor's tier boundaries (81/61/41/21) — a score of
// exactly 80 reads as a clean "A" even though it's one point under the
// top ("STRONGLY REPRESENTATIVE") color tier. Not a bug; don't "fix" one
// to match the other.
function getScoreGrade(score: number): string {
  if (score >= 80) return "A";
  if (score >= 60) return "B";
  if (score >= 40) return "C";
  if (score >= 20) return "D";
  return "F";
}

// v6.5: fundingDiversity folded into fundingIndependence as two of its
// five components (source breadth, industry concentration) — no longer
// its own scored dimension or card here. score_funding_diversity keeps
// being computed/stored for other consumers (e.g. Bluesky spotlight
// text), so it's deliberately NOT in this list, not an oversight.
const SCORE_KEYS: ScoreKey[] = [
  "fundingIndependence",
  "independentVoting",
  "legislativeEffectiveness",
];

const METRIC_BLURBS: Record<ScoreKey, string> = {
  fundingIndependence: "How little of their campaign comes from PACs, and how diversified their donor base is",
  independentVoting: "Does their voting match what their state elected them to do?",
  fundingDiversity: "How many different industries fund them",
  legislativeEffectiveness: "How well they advance bills they sponsor",
};

function ScoreBar({
  value,
  label,
  blurb,
  basis,
  entityType,
  entityId,
  dimensionKey,
}: {
  value: number;
  label: string;
  blurb: string;
  basis?: string;
  entityType?: "senator" | "representative";
  entityId?: string;
  dimensionKey?: ScoreKey;
}) {
  const bar = asciiScoreBar(value);

  const colorClass = getScoreColor(value);

  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3 text-sm">
        <div className="w-48 shrink-0" id={`score-label-${label.replace(/\s+/g, "-").toLowerCase()}`}>
          <span className="text-matrix-green/70">{label}</span>
          <div className="text-[10px] text-neon-cyan/50 leading-tight">{blurb}</div>
          {basis && <div className="text-[10px] text-matrix-green/40 italic leading-tight">{basis}</div>}
          {entityType && entityId && dimensionKey && (
            <ScoreBreakdownPanel
              entityType={entityType}
              entityId={entityId}
              dimensionKey={dimensionKey}
              label={label}
            />
          )}
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
              className={`block h-full ${getScoreBgColor(value)}`}
              style={{ width: `${value}%` }}
            />
          </span>
        </span>
        <span className={`text-right w-12 shrink-0 font-pixel ${colorClass}`} aria-hidden="true">{value}</span>
      </div>
    </div>
  );
}

export default function RepresentationScore({ breakdown, votingRecord, funding, sponsoredBills, rank, totalInChamber, entityId, chamber }: RepresentationScoreProps) {
  const entityType = chamber === "house" ? "representative" : "senator";
  const overall = breakdown.overall;
  const label = getScoreLabel(overall);
  const colorClass = getScoreColor(overall);
  const grade = getScoreGrade(overall);

  const votingBasis: string | undefined =
    !votingRecord || votingRecord.totalVotes === 0
      ? "no voting record · defaults to 50"
      : `${votingRecord.totalVotes} votes tracked`;

  // Surface the FI sub-components so the score is an auditable claim,
  // not a black-box number (matches the methodology on /about).
  const fundingIndependenceBasis: string | undefined = (() => {
    if (!funding || funding.totalRaised === 0) {
      return "no funding data · defaults to 50";
    }
    const pacPct = Math.round(((funding.totalFromPACs ?? 0) / funding.totalRaised) * 100);
    const smallPct = Math.round(funding.smallDonorPercentage ?? 0);
    const external = (funding.topDonors ?? []).filter(
      (d) => d.type !== "CandidateAffiliated" && d.type !== "Self-Funded",
    );
    const pool = external.reduce((a, d) => a + (d.total ?? 0), 0);
    const top10 = external.slice(0, 10).reduce((a, d) => a + (d.total ?? 0), 0);
    let basis = `${pacPct}% from PACs · ${smallPct}% small-donor`;
    if (external.length >= 20 && pool > 0) {
      basis += ` · top 10 donors hold ${Math.round((top10 / pool) * 100)}% of itemized pool`;
    }
    return basis;
  })();

  const nBills = sponsoredBills?.length ?? 0;
  const effectivenessBasis: string =
    nBills === 0
      ? "no bill data · defaults to 50"
      : nBills < 10
        ? `${nBills} bill${nBills !== 1 ? "s" : ""} sponsored · score shrunk toward 50`
        : `${nBills} bills sponsored`;

  const scoreBasis: Partial<Record<ScoreKey, string | undefined>> = {
    independentVoting: votingBasis,
    fundingIndependence: fundingIndependenceBasis,
    legislativeEffectiveness: effectivenessBasis,
  };

  return (
    <div>
      <div className="flex items-end gap-4 mb-1">
        <div className="flex items-baseline gap-2 shrink-0">
          <div className={`text-5xl sm:text-6xl font-pixel ${colorClass}`}>{overall}</div>
          <div className={`text-2xl sm:text-3xl font-pixel ${colorClass} opacity-70`} title={`Grade: ${grade}`}>{grade}</div>
        </div>
        <div className="pb-2 min-w-0">
          <div className="text-xs text-matrix-green/40">
            <MetricTooltip text="Weighted average of 3 sub-scores measuring how well this senator represents constituents. Based on funding independence (incl. donor-base diversity), voting alignment with their constituents, and legislative effectiveness. 100 = ideal representation, 0 = none. Scores near 50 mean limited data.">
              REPRESENTATION SCORECARD
            </MetricTooltip>
          </div>
          <div className={`text-sm font-pixel ${colorClass} tracking-wider break-words`}>{label}</div>
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
          const t = SCORE_TERMS[key];
          return (
            <ScoreBar
              key={key}
              value={breakdown[key]}
              label={t.label}
              blurb={METRIC_BLURBS[key]}
              basis={scoreBasis[key]}
              entityType={entityId ? entityType : undefined}
              entityId={entityId}
              dimensionKey={key}
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
