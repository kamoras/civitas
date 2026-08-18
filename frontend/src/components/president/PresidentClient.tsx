"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { fetchPresident, fetchPresidentLeaderboard } from "@/lib/api";
import { getScoreColor, getPresidentLabel } from "@/lib/representation";
import { MetricBar, StatBox } from "@/components/shared/ScoreMetric";
import ScoreTrendSection from "@/components/checker/ScoreTrendSection";
import StockTrades from "@/components/checker/StockTrades";
import type { President, PresidentLeaderboardEntry } from "@/types/president";

const PARTY_META: Record<string, { label: string; color: string; bg: string; border: string }> = {
  D: {
    label: "DEMOCRAT",
    color: "text-dem-blue",
    bg: "bg-dem-blue/20",
    border: "border-dem-blue/40",
  },
  R: {
    label: "REPUBLICAN",
    color: "text-signal-red",
    bg: "bg-signal-red/10",
    border: "border-signal-red/40",
  },
  DR: {
    label: "DEM-REPUBLICAN",
    color: "text-teal-400",
    bg: "bg-teal-400/20",
    border: "border-teal-400/40",
  },
  F: {
    label: "FEDERALIST",
    color: "text-ind-purple",
    bg: "bg-purple-400/20",
    border: "border-purple-400/40",
  },
  W: {
    label: "WHIG",
    color: "text-signal-amber",
    bg: "bg-signal-amber/10",
    border: "border-signal-amber/40",
  },
  I: { label: "INDEPENDENT", color: "text-ink", bg: "bg-white/10", border: "border-white/30" },
};

function getPartyMeta(party: string) {
  return (
    PARTY_META[party] ?? {
      label: party,
      color: "text-ink-lo",
      bg: "bg-white/10",
      border: "border-white/20",
    }
  );
}

const METRIC_LABELS: {
  key: "publicMandate" | "effectiveness" | "agencyAlignment" | "historicalLegacy";
  label: string;
  desc: string;
}[] = [
  {
    key: "publicMandate",
    label: "PUBLIC MANDATE",
    desc: "Approval polling (Truman onward) or, for earlier presidents, election-margin history. N/A for presidents who never won a presidential election.",
  },
  {
    key: "effectiveness",
    label: "EFFECTIVENESS",
    desc: "GDP growth, job creation, and tangible outcomes for voters",
  },
  {
    key: "agencyAlignment",
    label: "AGENCY ALIGNMENT",
    desc: "How effectively federal agencies execute the president's agenda through rulemaking. N/A before Federal Register data begins in 1936.",
  },
  {
    key: "historicalLegacy",
    label: "HISTORICAL LEGACY",
    desc: "C-SPAN's Presidential Historians Survey — crisis leadership, moral authority, and vision, as assessed by ~142 professional historians. N/A for any currently-serving or just-departed president; the survey only rates a completed term.",
  },
];

export function PresidentCard({ president }: { president: President }) {
  const overall = president.score.overall;
  const pm = getPartyMeta(president.party);
  const termEnd = president.termEnd ? president.termEnd.slice(0, 4) : "Present";

  return (
    <div className="terminal-window">
      <TerminalTitlebar title={`President no. ${president.number}`} />

      <div className="p-6 space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-mono text-ink-hi">
              {president.name}
              <span className="ml-2 text-ink-min text-sm">#{president.number}</span>
            </h2>
            <div className="flex items-center gap-3 mt-1">
              <span className={`text-xs px-2 py-0.5 border  ${pm.bg} ${pm.border} ${pm.color}`}>
                {pm.label}
              </span>
              <span className="text-ink-min text-xs">
                {president.termStart.slice(0, 4)}–{termEnd}
              </span>
              {president.isCurrent && (
                <span className="text-signal-amber text-xs animate-pulse border border-signal-amber/40 px-2 py-0.5">
                  CURRENT
                </span>
              )}
            </div>
          </div>
          <div className="text-right">
            {president.score.dimensionsAvailable === 0 ? (
              // A president with zero scored dimensions has no overall
              // score to show at all — compute_president_overall_score's
              // backend fallback of 0.0 exists so downstream sorting/math
              // never sees null, but 0 + getPresidentLabel(0)'s "FAILING"
              // reads as an actual (and the worst possible) score, not
              // "no data yet." Most common for a just-inaugurated
              // president before the first pipeline run.
              <div className="text-2xl font-bold text-ink-min">NOT YET CALCULATED</div>
            ) : (
              <>
                <div className={`text-4xl font-bold tabular-nums ${getScoreColor(overall)}`}>
                  {overall}
                </div>
                <div className={`text-xs tracking-widest ${getScoreColor(overall)}`}>
                  {getPresidentLabel(overall)}
                </div>
              </>
            )}
            <div
              className="text-xs text-ink-min mt-1"
              title="How many of the 4 possible score dimensions have data for this president — a score built from fewer is based on less information, not a worse president."
            >
              based on {president.score.dimensionsAvailable}/4 dimensions
            </div>
          </div>
        </div>

        {/* Source links */}
        <div className="flex flex-wrap gap-3">
          <a
            href="https://www.presidency.ucsb.edu/statistics/data/executive-orders"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [UCSB EXECUTIVE ORDERS]
          </a>
          <a
            href="https://www.presidency.ucsb.edu/statistics/data/presidential-job-approval"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [UCSB APPROVAL RATINGS]
          </a>
          <a
            href="https://www.presidency.ucsb.edu/statistics/data/presidential-election-mandates"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [UCSB ELECTION MARGINS]
          </a>
          <a
            href="https://www.measuringworth.com/datasets/usgdp/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [MEASURINGWORTH GDP]
          </a>
          <a
            href="https://data.bls.gov/timeseries/CES0000000001"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [BLS EMPLOYMENT DATA]
          </a>
          <a
            href="https://www.federalregister.gov/presidential-documents"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [FEDERAL REGISTER]
          </a>
          <a
            href="https://extapps2.oge.gov/201/Presiden.nsf"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [OGE FINANCIAL DISCLOSURES]
          </a>
          <a
            href="https://www.c-span.org/presidentsurvey2021/?page=overall"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-min hover:text-phos transition-colors"
          >
            [C-SPAN HISTORIANS SURVEY]
          </a>
        </div>

        {/* Score Breakdown */}
        <div>
          <h3 className="text-xs text-ink-lo tracking-widest mb-4">SCORE BREAKDOWN</h3>
          <div className="space-y-3">
            {METRIC_LABELS.map(({ key, label, desc }) => (
              <MetricBar
                key={key}
                label={label}
                value={president.score[key]}
                desc={desc}
                entityType="president"
                entityId={president.id}
                dimensionKey={key}
              />
            ))}
          </div>
        </div>

        {/* Score Trend */}
        <ScoreTrendSection entityId={president.id} entityType="president" />

        {/* Disclosed stock/crypto transactions — current president only.
            OGE Form 278-T filings exist only from the STOCK Act's 2012
            effective date onward and stop when a term ends, so rendering
            this for earlier presidents would show an empty section that
            reads as "traded nothing" rather than "no such filings exist."
            The section hides itself when there are no rows, so a
            just-inaugurated president sees nothing until the first
            filing lands. */}
        {president.isCurrent && <StockTrades politicianId={president.id} filer="president" />}

        {/* Key Stats */}
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-xs text-ink-lo tracking-widest">KEY METRICS</h3>
            <span className="text-xs text-ink-lo">
              Sources: UCSB, MeasuringWorth, BLS, Federal Register, C-SPAN
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <StatBox
              label="AVG APPROVAL"
              value={president.avgApproval != null ? `${president.avgApproval.toFixed(0)}` : null}
              unit="%"
            />
            <StatBox
              label="RECENT APPROVAL (90D)"
              value={
                president.recentAvgApproval != null
                  ? `${president.recentAvgApproval.toFixed(0)}`
                  : null
              }
              unit="%"
            />
            <StatBox
              label="ELECTION MARGIN"
              value={
                president.electionMargin != null ? `${president.electionMargin.toFixed(1)}` : null
              }
              unit="pt"
            />
            <StatBox
              label="GDP GROWTH"
              value={president.gdpGrowthAvg != null ? `${president.gdpGrowthAvg.toFixed(1)}` : null}
              unit="%/yr"
            />
            <StatBox
              label="JOBS"
              value={
                president.jobsCreatedMillions != null
                  ? `${president.jobsCreatedMillions > 0 ? "+" : ""}${president.jobsCreatedMillions.toFixed(1)}`
                  : null
              }
              unit="M"
            />
            <StatBox
              label="EXEC ORDERS"
              value={president.eoCount != null ? `${president.eoCount}` : null}
            />
            <StatBox
              label="C-SPAN SURVEY"
              value={
                president.historicalLegacyScore != null
                  ? `${president.historicalLegacyScore}`
                  : null
              }
              unit="pts"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function PresidentSelector({
  entries,
  selectedId,
  onSelect,
}: {
  entries: PresidentLeaderboardEntry[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-8 gap-1.5 mb-8">
      {entries.map((e) => {
        const active = e.id === selectedId;
        const pm = getPartyMeta(e.party);
        const partyBorder = active
          ? `${pm.border} ${pm.bg}`
          : `${pm.border.replace("/40", "/20")} hover:${pm.border}`;
        return (
          <button
            key={e.id}
            onClick={() => onSelect(e.id)}
            className={`px-2 py-1.5 border text-xs font-mono transition-all truncate ${partyBorder} ${
              active ? "text-ink-hi" : "text-ink-lo hover:text-ink"
            }`}
          >
            {e.name.split(" ").pop()}
            <span className="text-ink-min ml-1">#{e.number}</span>
          </button>
        );
      })}
    </div>
  );
}

export default function PresidentClient() {
  const searchParams = useSearchParams();
  const initialId = searchParams.get("id") ?? "";

  const [entries, setEntries] = useState<PresidentLeaderboardEntry[]>([]);
  const [selectedId, setSelectedId] = useState(initialId);
  const [president, setPresident] = useState<President | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPresidentLeaderboard()
      .then((data) => {
        setEntries(data);
        if (!selectedId && data.length > 0) {
          const current = data.find((e) => e.isCurrent);
          setSelectedId(current?.id ?? data[0].id);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    // Mount-only: selectedId excluded to avoid refetching the leaderboard on selection change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setDetailLoading(true);
    fetchPresident(selectedId)
      .then(setPresident)
      .catch((e) => setError(e.message))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  const chronological = useMemo(() => [...entries].sort((a, b) => a.number - b.number), [entries]);

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-signal-cyan animate-pulse text-lg">
          {">"} LOADING PRESIDENTIAL DATA...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-signal-red text-lg">{">"} ERROR</div>
        <div className="text-ink-min text-sm mt-2">{error}</div>
      </div>
    );
  }

  return (
    <div>
      <PresidentSelector entries={chronological} selectedId={selectedId} onSelect={setSelectedId} />

      {detailLoading && (
        <div className="terminal-window max-w-md mx-auto p-6 text-center">
          <div className="text-signal-cyan animate-pulse">{">"} LOADING PROFILE...</div>
        </div>
      )}

      {!detailLoading && president && (
        <div className="max-w-3xl mx-auto">
          <PresidentCard president={president} />
        </div>
      )}
    </div>
  );
}
