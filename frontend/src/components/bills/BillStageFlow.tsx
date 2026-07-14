"use client";

import { useConfig } from "@/hooks/useConfig";

export const MAIN_FLOW_STAGES = [
  "INTRODUCED",
  "IN_COMMITTEE",
  "PASSED_CHAMBER",
  "IN_OTHER_CHAMBER",
  "TO_PRESIDENT",
  "ENACTED",
];

export const ALL_STAGE_CODES = [...MAIN_FLOW_STAGES, "VETOED"];

// Below this, a nonzero bar would round to a sliver a couple pixels wide —
// floor it so it stays visible (and clickable) without lying about scale
// for the bars that actually earn their width.
const MIN_BAR_PCT = 2;

interface BillStageFlowProps {
  stageCounts: Record<string, number>;
  activeStage: string | null;
  onSelectStage: (stage: string | null) => void;
}

export default function BillStageFlow({ stageCounts, activeStage, onSelectStage }: BillStageFlowProps) {
  const config = useConfig();
  const stages = config?.billStages ?? {};
  const vetoedCount = stageCounts["VETOED"] || 0;

  const totalAll = Object.values(stageCounts).reduce((sum, n) => sum + n, 0) || 1;
  const maxCount = Math.max(1, ...MAIN_FLOW_STAGES.map((code) => stageCounts[code] || 0));

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-1">
        {MAIN_FLOW_STAGES.map((code) => {
          const info = stages[code];
          const count = stageCounts[code] || 0;
          const pct = count > 0 ? Math.max((count / maxCount) * 100, MIN_BAR_PCT) : 0;
          const share = totalAll > 0 ? Math.round((count / totalAll) * 100) : 0;
          const color = info?.color ?? "#00ff41";
          const isActive = activeStage === code;

          return (
            <button
              key={code}
              type="button"
              onClick={() => onSelectStage(isActive ? null : code)}
              aria-pressed={isActive}
              title={`${info?.name ?? code}: ${count.toLocaleString()} bill${count === 1 ? "" : "s"} (${share}% of all bills tracked)`}
              className={`group flex items-center gap-3 py-1.5 px-2 -mx-2 rounded transition-colors text-left ${
                isActive ? "bg-white/5" : "hover:bg-white/[0.03]"
              }`}
              style={isActive ? { boxShadow: `inset 2px 0 0 0 ${color}` } : undefined}
            >
              <span
                className="w-28 sm:w-32 shrink-0 truncate text-[10px] font-mono uppercase tracking-wider"
                style={{ color: isActive ? color : undefined }}
              >
                {info?.name ?? code}
              </span>

              <span className="relative flex-1 h-3.5 rounded-sm bg-matrix-green/[0.06] overflow-hidden">
                <span
                  className="absolute inset-y-0 left-0 rounded-r-[4px] transition-[width] duration-300"
                  style={{ width: `${pct}%`, backgroundColor: color, opacity: isActive ? 1 : 0.75 }}
                />
              </span>

              <span className="w-20 sm:w-24 shrink-0 text-right font-mono text-[11px] tabular-nums text-matrix-green/70">
                {count.toLocaleString()}
                <span className="hidden sm:inline text-matrix-green/30"> · {share}%</span>
              </span>
            </button>
          );
        })}
      </div>

      {vetoedCount > 0 && (
        <button
          type="button"
          onClick={() => onSelectStage(activeStage === "VETOED" ? null : "VETOED")}
          className={`text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded border transition-colors ${
            activeStage === "VETOED"
              ? "border-rep-red text-rep-red bg-rep-red/10"
              : "border-rep-red/20 text-rep-red/50 hover:text-rep-red/80"
          }`}
        >
          {vetoedCount} vetoed
        </button>
      )}
    </div>
  );
}
