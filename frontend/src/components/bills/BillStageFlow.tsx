"use client";

import { useConfig } from "@/hooks/useConfig";

const MAIN_FLOW_STAGES = [
  "INTRODUCED",
  "IN_COMMITTEE",
  "PASSED_CHAMBER",
  "IN_OTHER_CHAMBER",
  "TO_PRESIDENT",
  "ENACTED",
];

interface BillStageFlowProps {
  stageCounts: Record<string, number>;
  activeStage: string | null;
  onSelectStage: (stage: string | null) => void;
}

export default function BillStageFlow({ stageCounts, activeStage, onSelectStage }: BillStageFlowProps) {
  const config = useConfig();
  const stages = config?.billStages ?? {};
  const vetoedCount = stageCounts["VETOED"] || 0;

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto pb-2">
        <div className="flex items-center min-w-max px-1">
          {MAIN_FLOW_STAGES.map((code, i) => {
            const info = stages[code];
            const count = stageCounts[code] || 0;
            const isActive = activeStage === code;
            return (
              <div key={code} className="flex items-center">
                <button
                  type="button"
                  onClick={() => onSelectStage(isActive ? null : code)}
                  className={`group relative flex flex-col items-center justify-center w-28 h-24 rounded-lg border transition-all ${
                    isActive
                      ? "border-current bg-white/5 scale-105"
                      : "border-matrix-green/15 hover:border-matrix-green/40"
                  }`}
                  style={{ color: info?.color ?? "#00ff41" }}
                  aria-pressed={isActive}
                >
                  <span className="font-pixel text-xl">{count}</span>
                  <span className="mt-2 text-center text-[9px] font-mono uppercase tracking-wider text-matrix-green/60 px-1">
                    {info?.name ?? code}
                  </span>
                  {isActive && (
                    <span className="absolute -top-1.5 -right-1.5 w-3 h-3 rounded-full bg-current animate-pulse-neon" />
                  )}
                </button>
                {i < MAIN_FLOW_STAGES.length - 1 && (
                  <div className="relative w-10 h-px mx-1 bg-matrix-green/15 overflow-visible shrink-0">
                    <span
                      className="absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-neon-cyan shadow-[0_0_6px_2px_rgba(0,255,255,0.6)] motion-reduce:hidden animate-flow-pulse"
                      style={{ animationDelay: `${i * 0.35}s` }}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
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
