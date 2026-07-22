import { getScoreColor, getScoreBgColor } from "@/lib/representation";
import ScoreBreakdownPanel, { type BreakdownEntityType } from "@/components/shared/ScoreBreakdownPanel";

/**
 * Score metric bar + stat cell shared by the President and Justice detail
 * clients, which each had verbatim copies. `entityType` routes the
 * "show the math" ScoreBreakdownPanel. `value` may be null — every
 * dimension is either computed from real data or genuinely inapplicable
 * for this entity (never a fabricated placeholder), and a null value
 * renders as "N/A" instead of a bar.
 */

export function MetricBar({
  label,
  value,
  desc,
  entityType,
  entityId,
  dimensionKey,
}: {
  label: string;
  value: number | null;
  desc: string;
  entityType: BreakdownEntityType;
  entityId?: string;
  dimensionKey?: string;
}) {
  const color = value != null ? getScoreBgColor(value) : "";

  return (
    <div className="group">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-matrix-green/60 tracking-widest">{label}</span>
        <span
          className={`text-sm font-bold tabular-nums ${value != null ? getScoreColor(value) : "text-matrix-green/30"}`}
          aria-hidden="true"
        >
          {value != null ? value : "N/A"}
        </span>
      </div>
      <div
        className="w-full h-2 bg-white/10 rounded-full overflow-hidden"
        role="progressbar"
        aria-valuenow={value ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={value != null ? `${label}: ${value} out of 100. ${desc}` : `${label}: not applicable. ${desc}`}
      >
        {value != null && (
          <div
            className={`h-full rounded-full ${color} transition-all duration-700`}
            style={{ width: `${value}%` }}
          />
        )}
      </div>
      <p className="text-[10px] text-matrix-green/50 mt-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        {desc}
      </p>
      {entityId && dimensionKey && value != null && (
        <ScoreBreakdownPanel entityType={entityType} entityId={entityId} dimensionKey={dimensionKey} label={label} />
      )}
    </div>
  );
}

export function StatBox({
  label,
  value,
  unit,
}: {
  label: string;
  value: string | null;
  unit?: string;
}) {
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
