"use client";

import { useConfig } from "@/hooks/useConfig";
import { billStageStyle } from "@/lib/billStages";

export const MAIN_FLOW_STAGES = [
  "INTRODUCED",
  "REFERRED",
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

export default function BillStageFlow({
  stageCounts,
  activeStage,
  onSelectStage,
}: BillStageFlowProps) {
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
          const style = billStageStyle(code);
          const isActive = activeStage === code;

          return (
            <button
              key={code}
              type="button"
              onClick={() => onSelectStage(isActive ? null : code)}
              aria-pressed={isActive}
              title={`${info?.name ?? code}: ${count.toLocaleString()} bill${count === 1 ? "" : "s"} (${share}% of all bills tracked)`}
              className={`group -mx-2 flex items-center gap-3 border-l-2 px-2 py-1.5 text-left transition-colors ${
                isActive ? `bg-white/5 ${style.rule}` : "border-l-transparent hover:bg-white/[0.03]"
              }`}
            >
              {/* w-40 (160px). The previous w-32 was sized against "IN OTHER
                  CHAMBER" at ~114px, but that is not the longest label —
                  "Referred to Committee" measures 149px at 12px Share Tech
                  Mono with this tracking, so the one stage the funnel exists
                  to distinguish from IN_COMMITTEE rendered as "REFERRED TO
                  COMMI…". `truncate` stays as a guard for a longer stage name
                  arriving from /config, not as the normal case. */}
              <span
                className={`w-40 shrink-0 truncate text-xs font-mono uppercase tracking-wider ${
                  isActive ? style.text : "text-ink-lo"
                }`}
              >
                {info?.name ?? code}
              </span>

              <span className="relative flex-1 h-3.5 bg-phos/[0.06] overflow-hidden">
                <span
                  className={`absolute inset-y-0 left-0 transition-[width] duration-300 ${style.bar} ${
                    isActive ? "opacity-100" : "opacity-75"
                  }`}
                  style={{ width: `${pct}%` }}
                />
              </span>

              {/* w-12 on mobile, not w-20: the share ("· 16%") is
                  `hidden sm:inline`, so below `sm` this column reserved 80px
                  to render at most four digits. Giving the 32px back to the
                  bar matters at 390px, where the bar was down to 55px. */}
              <span className="w-12 shrink-0 text-right font-mono text-xs tabular-nums text-ink sm:w-24">
                {count.toLocaleString()}
                <span className="hidden sm:inline text-ink-min"> · {share}%</span>
              </span>
            </button>
          );
        })}
      </div>

      {vetoedCount > 0 && (
        <button
          type="button"
          onClick={() => onSelectStage(activeStage === "VETOED" ? null : "VETOED")}
          className={`border px-2 py-1 font-mono text-xs uppercase tracking-wider transition-colors ${
            activeStage === "VETOED"
              ? "border-signal-red bg-signal-red/10 text-signal-red"
              : "border-signal-red/40 text-signal-red hover:border-signal-red"
          }`}
        >
          {vetoedCount} vetoed
        </button>
      )}
    </div>
  );
}
