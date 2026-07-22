"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { fetchPresident, fetchPresidentLeaderboard } from "@/lib/api";
import { getScoreColor, getPresidentLabel } from "@/lib/representation";
import { MetricBar, StatBox } from "@/components/shared/ScoreMetric";
import ScoreTrendSection from "@/components/checker/ScoreTrendSection";
import type { President, PresidentLeaderboardEntry } from "@/types/president";

const PARTY_META: Record<string, { label: string; color: string; bg: string; border: string }> = {
  D:  { label: "DEMOCRAT",          color: "text-dem-blue",    bg: "bg-dem-blue/20",    border: "border-dem-blue/40" },
  R:  { label: "REPUBLICAN",       color: "text-rep-red",     bg: "bg-rep-red/20",     border: "border-rep-red/40" },
  DR: { label: "DEM-REPUBLICAN",   color: "text-teal-400",    bg: "bg-teal-400/20",    border: "border-teal-400/40" },
  F:  { label: "FEDERALIST",       color: "text-purple-400",  bg: "bg-purple-400/20",  border: "border-purple-400/40" },
  W:  { label: "WHIG",             color: "text-amber-400",   bg: "bg-amber-400/20",   border: "border-amber-400/40" },
  I:  { label: "INDEPENDENT",      color: "text-white/70",    bg: "bg-white/10",       border: "border-white/30" },
};

function getPartyMeta(party: string) {
  return PARTY_META[party] ?? { label: party, color: "text-white/50", bg: "bg-white/10", border: "border-white/20" };
}

const METRIC_LABELS: { key: keyof President["score"]; label: string; desc: string; alwaysEstimate?: boolean }[] = [
  { key: "publicMandate", label: "PUBLIC MANDATE", desc: "Approval trajectory and coalition retention", alwaysEstimate: true },
  { key: "effectiveness", label: "EFFECTIVENESS", desc: "GDP growth, job creation, and tangible outcomes for voters" },
  { key: "competence", label: "COMPETENCE", desc: "Executive order activity rate and administrative execution. Court-success and cabinet-turnover rates have no live data source and are not currently part of this score." },
  { key: "agencyAlignment", label: "AGENCY ALIGNMENT", desc: "How effectively federal agencies execute the president's agenda through rulemaking" },
];

export function PresidentCard({ president }: { president: President }) {
  const overall = president.score.overall;
  const pm = getPartyMeta(president.party);
  const termEnd = president.termEnd ? president.termEnd.slice(0, 4) : "Present";

  return (
    <div className="terminal-window">
      <TerminalTitlebar title={`president_${president.number}.profile`} />

      <div className="p-6 space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-terminal text-white">
              {president.name}
              <span className="ml-2 text-matrix-green/30 text-sm">#{president.number}</span>
            </h2>
            <div className="flex items-center gap-3 mt-1">
              <span className={`text-xs px-2 py-0.5 border rounded-sm ${pm.bg} ${pm.border} ${pm.color}`}>
                {pm.label}
              </span>
              <span className="text-matrix-green/40 text-xs">
                {president.termStart.slice(0, 4)}–{termEnd}
              </span>
              {president.isCurrent && (
                <span className="text-neon-yellow text-xs animate-pulse border border-neon-yellow/30 px-2 py-0.5">
                  CURRENT
                </span>
              )}
            </div>
          </div>
          <div className="text-right">
            <div className={`text-4xl font-bold tabular-nums ${getScoreColor(overall)}`}>{overall}</div>
            <div className={`text-xs tracking-widest ${getScoreColor(overall)}`}>
              {getPresidentLabel(overall)}
            </div>
          </div>
        </div>

        {/* Summary */}
        <div className="border border-matrix-green/10 bg-matrix-green/5 p-4">
          <p className="text-sm text-matrix-green/70 leading-relaxed">{president.summary}</p>
        </div>

        {/* Source links */}
        <div className="flex flex-wrap gap-3">
          <a
            href="https://www.federalregister.gov/presidential-documents"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [FEDERAL REGISTER]
          </a>
          <a
            href="https://data.bls.gov/timeseries/CES0000000001"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [BLS EMPLOYMENT DATA]
          </a>
          <a
            href="https://www.bea.gov/data/gdp/gross-domestic-product"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [BEA GDP DATA]
          </a>
          <a
            href="https://www.presidency.ucsb.edu/statistics/data/presidential-job-approval"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [APPROVAL RATINGS]
          </a>
        </div>

        {/* Score Breakdown */}
        <div>
          <h3 className="text-xs text-matrix-green/50 tracking-widest mb-4">SCORE BREAKDOWN</h3>
          <div className="space-y-3">
            {METRIC_LABELS.map(({ key, label, desc, alwaysEstimate }) => (
              <MetricBar
                key={key}
                label={label}
                value={president.score[key]}
                desc={desc}
                entityType="president"
                isEstimate={alwaysEstimate || (key === "competence" && !president.competenceHasLiveData)}
                entityId={president.id}
                dimensionKey={key}
              />
            ))}
          </div>
        </div>

        {/* Score Trend */}
        <ScoreTrendSection entityId={president.id} entityType="president" />

        {/* Key Stats */}
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-xs text-matrix-green/50 tracking-widest">KEY METRICS</h3>
            <span className="text-[10px] text-matrix-green/50">
              Sources: BLS, BEA, Federal Register
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <StatBox
              label="AVG APPROVAL"
              value={president.avgApproval != null ? `${president.avgApproval.toFixed(0)}` : null}
              unit="%"
              isEstimate
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
              label="EO COURT WIN"
              value={president.eoCourtSuccessPct != null ? `${president.eoCourtSuccessPct.toFixed(0)}` : null}
              unit="%"
              isEstimate
            />
            <StatBox
              label="CABINET TURNOVER"
              value={president.cabinetTurnoverPct != null ? `${president.cabinetTurnoverPct.toFixed(0)}` : null}
              unit="%"
              isEstimate
            />
          </div>
        </div>

        {/* Achievements and Failures */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {president.keyAchievements.length > 0 && (
            <div>
              <h3 className="text-xs text-matrix-green/50 tracking-widest mb-2">KEY ACHIEVEMENTS</h3>
              <ul className="space-y-1">
                {president.keyAchievements.map((a, i) => (
                  <li key={i} className="text-xs text-matrix-green/60 flex items-start gap-2">
                    <span className="text-matrix-green shrink-0">+</span>
                    <span>{a}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {president.keyFailures.length > 0 && (
            <div>
              <h3 className="text-xs text-red-400/50 tracking-widest mb-2">KEY FAILURES</h3>
              <ul className="space-y-1">
                {president.keyFailures.map((f, i) => (
                  <li key={i} className="text-xs text-red-400/60 flex items-start gap-2">
                    <span className="text-red-400 shrink-0">-</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
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
            className={`px-2 py-1.5 border text-xs font-terminal transition-all truncate ${partyBorder} ${
              active ? "text-white" : "text-white/40 hover:text-white/70"
            }`}
          >
            {e.name.split(" ").pop()}
            <span className="text-matrix-green/30 ml-1">#{e.number}</span>
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

  const chronological = useMemo(
    () => [...entries].sort((a, b) => a.number - b.number),
    [entries],
  );

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-neon-cyan animate-pulse text-lg">{">"} LOADING PRESIDENTIAL DATA...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-red-500 text-lg">{">"} ERROR</div>
        <div className="text-matrix-green/40 text-sm mt-2">{error}</div>
      </div>
    );
  }

  return (
    <div>
      <PresidentSelector entries={chronological} selectedId={selectedId} onSelect={setSelectedId} />

      {detailLoading && (
        <div className="terminal-window max-w-md mx-auto p-6 text-center">
          <div className="text-neon-cyan animate-pulse">{">"} LOADING PROFILE...</div>
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
