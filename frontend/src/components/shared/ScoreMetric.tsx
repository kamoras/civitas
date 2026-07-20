import { getScoreColor, getScoreBgColor } from "@/lib/representation";
import ScoreBreakdownPanel, { type BreakdownEntityType } from "@/components/shared/ScoreBreakdownPanel";

/**
 * Score metric bar + stat cell shared by the President and Justice detail
 * clients, which each had verbatim copies. `entityType` routes the
 * "show the math" ScoreBreakdownPanel; `isEstimate` renders the editorial-
 * estimate marker (used only where no live data source exists).
 */

export function MetricBar({
  label,
  value,
  desc,
  entityType,
  entityId,
  dimensionKey,
  isEstimate,
}: {
  label: string;
  value: number;
  desc: string;
  entityType: BreakdownEntityType;
  entityId?: string;
  dimensionKey?: string;
  isEstimate?: boolean;
}) {
  const color = getScoreBgColor(value);

  return (
    <div className="group">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-matrix-green/60 tracking-widest">
          {label}
          {isEstimate && (
            <span
              className="ml-1.5 text-neon-yellow/70 normal-case tracking-normal"
              title="No live data source exists for this metric — it's a one-time editorial estimate, not computed from an API."
            >
              · editorial estimate
            </span>
          )}
        </span>
        <span className={`text-sm font-bold tabular-nums ${getScoreColor(value)}`} aria-hidden="true">{value}</span>
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
      <p className="text-[10px] text-matrix-green/50 mt-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        {desc}
      </p>
      {entityId && dimensionKey && (
        <ScoreBreakdownPanel entityType={entityType} entityId={entityId} dimensionKey={dimensionKey} label={label} />
      )}
    </div>
  );
}

export function StatBox({
  label,
  value,
  unit,
  isEstimate,
}: {
  label: string;
  value: string | null;
  unit?: string;
  isEstimate?: boolean;
}) {
  return (
    <div className="border border-matrix-green/20 bg-terminal-bg/50 px-3 py-2 text-center">
      <div className="text-[10px] text-matrix-green/40 tracking-widest mb-1">
        {label}
        {isEstimate && (
          <span
            className="text-neon-yellow/70"
            title="No live data source exists for this figure — it's a one-time editorial estimate, not tracked or updated from an API."
          >
            {" "}*
          </span>
        )}
      </div>
      <div className="text-lg font-bold text-white/80 tabular-nums">
        {value ?? "—"}
        {unit && value && <span className="text-xs text-matrix-green/40 ml-0.5">{unit}</span>}
      </div>
    </div>
  );
}
