"use client";

import { useEffect, useState } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { fetchJustice, fetchJusticeLeaderboard } from "@/lib/api";
import { calculateJusticeScore, getJusticeLabel, getScoreColor, getScoreBgColor } from "@/lib/representation";
import ScoreBreakdownPanel from "@/components/shared/ScoreBreakdownPanel";
import type { Justice, JusticeLeaderboardEntry, JusticeScore } from "@/types/justice";

const PARTY_BADGE: Record<string, { label: string; color: string; bg: string; border: string }> = {
  R: { label: "R-APPOINTED", color: "text-rep-red", bg: "bg-rep-red/20", border: "border-rep-red/40" },
  D: { label: "D-APPOINTED", color: "text-dem-blue", bg: "bg-dem-blue/20", border: "border-dem-blue/40" },
};

function getPartyBadge(party: string | null) {
  if (!party) return { label: "UNKNOWN", color: "text-white/50", bg: "bg-white/10", border: "border-white/20" };
  return PARTY_BADGE[party] ?? { label: party, color: "text-white/50", bg: "bg-white/10", border: "border-white/20" };
}

const METRIC_LABELS: { key: keyof JusticeScore; label: string; desc: string }[] = [
  {
    key: "consistency",
    label: "IDEOLOGICAL CONSISTENCY",
    desc: "How unpredictable are their votes? Low bloc-alignment = high consistency. A justice driven purely by ideology scores LOW here.",
  },
  {
    key: "independence",
    label: "INDEPENDENCE",
    desc: "How often they break from their appointing-party's expected voting bloc in split decisions.",
  },
  {
    key: "bipartisanAgreement",
    label: "BIPARTISAN AGREEMENT",
    desc: "Fraction of cases decided unanimously or near-unanimously — reflects jurisprudential pragmatism.",
  },
  {
    key: "judicialRestraint",
    label: "JUDICIAL RESTRAINT",
    desc: "Balanced dissent patterns — measured disagreement rather than ideological grandstanding.",
  },
];

function MetricBar({ label, value, desc, entityId, dimensionKey }: { label: string; value: number; desc: string; entityId?: string; dimensionKey?: string }) {
  const color = getScoreBgColor(value);

  return (
    <div className="group">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-matrix-green/60 tracking-widest">{label}</span>
        <span className={`text-sm font-bold tabular-nums ${getScoreColor(value)}`}>{value}</span>
      </div>
      <div
        className="w-full h-2 bg-white/10 rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label}: ${value} out of 100. ${desc}`}
      >
        <div
          className={`h-full rounded-full ${color} transition-all duration-700`}
          style={{ width: `${value}%` }}
        />
      </div>
      <p className="text-[10px] text-matrix-green/50 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        {desc}
      </p>
      {entityId && dimensionKey && (
        <ScoreBreakdownPanel entityType="justice" entityId={entityId} dimensionKey={dimensionKey} label={label} />
      )}
    </div>
  );
}

function StatBox({ label, value, unit }: { label: string; value: string | null; unit?: string }) {
  return (
    <div className="border border-matrix-green/20 bg-terminal-bg/50 px-3 py-2 text-center">
      <div className="text-[10px] text-matrix-green/40 tracking-widest mb-1">{label}</div>
      <div className="text-lg font-bold text-white/80 tabular-nums">
        {value ?? "—"}
        {unit && value && <span className="text-xs text-matrix-green/40 ml-0.5">{unit}</span>}
      </div>
    </div>
  );
}

function AgreementRow({ name, pct }: { name: string; pct: number }) {
  const label = name
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  const color = getScoreColor(pct);
  const barColor = getScoreBgColor(pct);

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-white/60 w-36 truncate">{label}</span>
      <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs font-bold tabular-nums ${color} w-10 text-right`}>{pct}%</span>
    </div>
  );
}

export function JusticeCard({ justice }: { justice: Justice }) {
  const overall = calculateJusticeScore(justice.score);
  const pb = getPartyBadge(justice.appointingParty);

  const agreementEntries = Object.entries(justice.agreementMatrix)
    .sort(([, a], [, b]) => b - a);

  return (
    <div className="terminal-window">
      <TerminalTitlebar title={`justice_${justice.lastName.toLowerCase()}.profile`} />

      <div className="p-6 space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-terminal text-white">{justice.name}</h2>
            <div className="flex items-center gap-3 mt-1 flex-wrap">
              <span className="text-xs text-matrix-green/60">{justice.roleTitle}</span>
              <span className={`text-xs px-2 py-0.5 border rounded-sm ${pb.bg} ${pb.border} ${pb.color}`}>
                {pb.label}
              </span>
              {justice.appointingPresident && (
                <span className="text-xs text-white/30">
                  by {justice.appointingPresident}
                </span>
              )}
            </div>
          </div>
          <div className="text-right">
            <div className={`text-4xl font-bold tabular-nums ${getScoreColor(overall)}`}>{overall}</div>
            <div className={`text-xs tracking-widest ${getScoreColor(overall)}`}>
              {getJusticeLabel(overall)}
            </div>
          </div>
        </div>

        {/* Summary */}
        {justice.summary && (
          <div className="border border-matrix-green/10 bg-matrix-green/5 p-4">
            <p className="text-sm text-matrix-green/70 leading-relaxed">{justice.summary}</p>
          </div>
        )}

        {/* Methodology note */}
        <div className="border border-white/5 bg-white/[0.02] p-3">
          <p className="text-[10px] text-white/30 leading-relaxed">
            Scores derived from Martin-Quinn-style voting pattern analysis (Martin &amp; Quinn, 2002).
            Consistency measures how unpredictable a justice&apos;s votes are relative to ideological bloc —
            higher is better (follows law, not party). Based on {justice.casesDecided} cases from recent
            SCOTUS terms via Oyez.
          </p>
        </div>

        {/* Source links */}
        <div className="flex flex-wrap gap-3">
          <a
            href="https://www.oyez.org"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [OYEZ PROJECT]
          </a>
          <a
            href="https://www.supremecourt.gov"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [SUPREME COURT]
          </a>
          <a
            href="https://mqscores.lsa.umich.edu"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-matrix-green/30 hover:text-neon-cyan transition-colors"
          >
            [MARTIN-QUINN SCORES]
          </a>
        </div>

        {/* Score Breakdown */}
        <div>
          <h3 className="text-xs text-matrix-green/50 tracking-widest mb-4">JURISPRUDENTIAL CONSISTENCY</h3>
          <div className="space-y-3">
            {METRIC_LABELS.map(({ key, label, desc }) => (
              <MetricBar key={key} label={label} value={justice.score[key]} desc={desc} entityId={justice.id} dimensionKey={key} />
            ))}
          </div>
        </div>

        {/* Key Stats */}
        <div>
          <h3 className="text-xs text-matrix-green/50 tracking-widest mb-3">VOTING PROFILE</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <StatBox label="CASES" value={`${justice.casesDecided}`} />
            <StatBox label="MAJORITY" value={`${justice.majorityPct.toFixed(0)}`} unit="%" />
            <StatBox label="DISSENT" value={`${justice.dissentPct.toFixed(0)}`} unit="%" />
            <StatBox label="UNANIMOUS" value={`${justice.unanimousPct.toFixed(0)}`} unit="%" />
            <StatBox label="CROSS-BLOC" value={`${justice.crossBlocPct.toFixed(0)}`} unit="%" />
            <StatBox label="CLOSE CASE MAJ" value={`${justice.closeCaseMajorityPct.toFixed(0)}`} unit="%" />
          </div>
        </div>

        {/* Opinions authored */}
        <div>
          <h3 className="text-xs text-matrix-green/50 tracking-widest mb-3">OPINIONS AUTHORED</h3>
          <div className="grid grid-cols-3 gap-2">
            <StatBox label="MAJORITY" value={`${justice.authoredMajority}`} />
            <StatBox label="DISSENT" value={`${justice.authoredDissent}`} />
            <StatBox label="CONCURRENCE" value={`${justice.authoredConcurrence}`} />
          </div>
        </div>

        {/* Agreement Matrix */}
        {agreementEntries.length > 0 && (
          <div>
            <h3 className="text-xs text-matrix-green/50 tracking-widest mb-3">
              AGREEMENT WITH OTHER JUSTICES
            </h3>
            <div className="space-y-2">
              {agreementEntries.map(([id, pct]) => (
                <AgreementRow key={id} name={id} pct={pct} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function JusticeSelector({
  entries,
  selectedId,
  onSelect,
}: {
  entries: JusticeLeaderboardEntry[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 lg:grid-cols-9 gap-1.5 mb-8">
      {entries.map((e) => {
        const active = e.id === selectedId;
        const pb = getPartyBadge(e.appointingParty);
        const overall = calculateJusticeScore(e.score);
        return (
          <button
            key={e.id}
            onClick={() => onSelect(e.id)}
            className={`px-2 py-2.5 border text-xs font-terminal transition-all ${
              active
                ? `${pb.border} ${pb.bg} text-white`
                : `${pb.border.replace("/40", "/20")} text-white/40 hover:text-white/70 hover:${pb.border}`
            }`}
          >
            <div className="truncate">{e.lastName}</div>
            <div className={`text-[10px] tabular-nums ${getScoreColor(overall)}`}>{overall}</div>
          </button>
        );
      })}
    </div>
  );
}

export default function JusticeClient() {
  const [entries, setEntries] = useState<JusticeLeaderboardEntry[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [justice, setJustice] = useState<Justice | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJusticeLeaderboard()
      .then((data) => {
        setEntries(data);
        if (!selectedId && data.length > 0) {
          setSelectedId(data[0].id);
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
    fetchJustice(selectedId)
      .then(setJustice)
      .catch((e) => setError(e.message))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  if (loading) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-neon-cyan animate-pulse text-lg">{">"} LOADING SCOTUS DATA...</div>
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

  if (entries.length === 0) {
    return (
      <div className="terminal-window max-w-md mx-auto p-6 text-center">
        <div className="text-matrix-green/40 text-sm">
          {">"} No justice data available yet. Run the justice pipeline to populate.
        </div>
      </div>
    );
  }

  return (
    <div>
      <JusticeSelector entries={entries} selectedId={selectedId} onSelect={setSelectedId} />

      {detailLoading && (
        <div className="terminal-window max-w-md mx-auto p-6 text-center">
          <div className="text-neon-cyan animate-pulse">{">"} LOADING PROFILE...</div>
        </div>
      )}

      {!detailLoading && justice && (
        <div className="max-w-3xl mx-auto">
          <JusticeCard justice={justice} />
        </div>
      )}
    </div>
  );
}
