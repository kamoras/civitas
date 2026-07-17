import { PartisanDepth } from "@/types/senator";
import { usePolicyLabel } from "@/hooks/useConfig";
import CollapsibleSection from "../shared/CollapsibleSection";
import MetricTooltip from "./MetricTooltip";

interface PlatformTrackerProps {
  platformSummary: string;
  partisanDepth: PartisanDepth | null;
  senatorParty: "D" | "R" | "I";
}

const DEPTH_STYLES = {
  deep: { text: "text-neon-pink", label: "DEEPLY PARTISAN" },
  moderate: { text: "text-yellow-500", label: "MODERATELY PARTISAN" },
  centrist: { text: "text-matrix-green", label: "CENTRIST" },
  "cross-cutting": { text: "text-neon-cyan", label: "CROSS-CUTTING" },
};

function PolicyLabel({ area }: { area: string }) {
  const label = usePolicyLabel(area);
  return <>{label}</>;
}

function PartisanDepthPanel({ depth, senatorParty }: { depth: PartisanDepth; senatorParty: string }) {
  const depthStyle = DEPTH_STYLES[depth.depth];
  const leanPct = Math.min(Math.abs(depth.overallLean) / 0.15 * 100, 100);
  const leanDirection = depth.overallLean > 0 ? "R" : depth.overallLean < 0 ? "D" : "center";

  const matchesParty = depth.overallParty === senatorParty;
  const oppositeParty = senatorParty === "R" ? "D" : "R";

  return (
    <div className="terminal-window p-4 mb-4">
      <div className="flex items-baseline justify-between mb-3">
        <h4 className="text-sm font-pixel text-neon-cyan">{">"} <MetricTooltip text="Measures how partisan this senator's actual votes are. Analyzes roll-call votes on bills and compares them against each party's platform positions. Based on what they voted for, not what they say.">PARTISAN DEPTH ANALYSIS</MetricTooltip></h4>
        <span className={`text-xs font-pixel ${depthStyle.text}`}>{depthStyle.label}</span>
      </div>

      {/* Spectrum bar */}
      <div className="mb-3">
        <div className="flex justify-between text-[10px] mb-1">
          <span className="text-blue-400 font-pixel">← DEM</span>
          <span className="text-matrix-green/30 font-pixel">CENTER</span>
          <span className="text-red-400 font-pixel">REP →</span>
        </div>
        <div className="relative h-3 bg-matrix-green/5 border border-matrix-green/20">
          <div className="absolute top-0 bottom-0 left-1/2 w-px bg-matrix-green/20" />
          <div
            className={`absolute top-0 bottom-0 ${leanDirection === "R" ? "bg-red-500/40" : "bg-blue-500/40"}`}
            style={{
              left: leanDirection === "R" ? "50%" : `${50 - leanPct / 2}%`,
              width: `${leanPct / 2}%`,
            }}
          />
          <div
            className="absolute top-0 bottom-0 w-1 bg-matrix-green"
            style={{
              left: `${50 + (depth.overallLean / 0.15) * 50}%`,
              transform: "translateX(-50%)",
            }}
          />
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 mb-3 text-center">
        <div className="terminal-window p-2 min-w-0">
          <div className={`text-sm font-pixel ${depth.overallParty === "R" ? "text-red-400" : depth.overallParty === "D" ? "text-blue-400" : "text-matrix-green"}`}>
            {depth.overallParty === "centrist" ? "CTR" : depth.overallParty}
          </div>
          <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Overall ideological direction derived from roll-call votes. R = votes lean Republican, D = votes lean Democrat, CTR = centrist.">LEAN</MetricTooltip></div>
        </div>
        <div className="terminal-window p-2 min-w-0">
          <div className="text-sm font-pixel text-matrix-green">{depth.totalPositions}</div>
          <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Number of policy areas where this senator has cast votes. Each area's lean is derived from how they voted on D-leaning vs R-leaning bills in that area.">AREAS</MetricTooltip></div>
        </div>
        <div className="terminal-window p-2 min-w-0">
          <div className={`text-sm font-pixel ${depth.crossPartyCount > 0 ? "text-neon-cyan" : "text-matrix-green/40"}`}>
            {depth.crossPartyCount}
          </div>
          <div className="text-[10px] text-matrix-green/40"><MetricTooltip text="Number of policy areas where this senator's votes align with the opposite party's platform. Higher = more ideologically independent.">CROSS</MetricTooltip></div>
        </div>
      </div>

      {/* Party alignment interpretation */}
      {senatorParty !== "I" && (
        <div className="text-xs text-matrix-green/70 mb-3">
          {matchesParty ? (
            depth.depth === "deep" ? (
              <span>Voting record is <span className={depthStyle.text}>strongly aligned</span> with {senatorParty === "R" ? "Republican" : "Democratic"} positions.</span>
            ) : depth.depth === "cross-cutting" ? (
              <span>Despite being {senatorParty === "R" ? "Republican" : "Democrat"}, this senator voted with {oppositeParty === "R" ? "Republicans" : "Democrats"} in <span className="text-neon-cyan">{depth.crossPartyCount} policy areas</span>.</span>
            ) : (
              <span>Voting record shows <span className={depthStyle.text}>{depth.depth}</span> alignment with {senatorParty === "R" ? "Republican" : "Democratic"} positions.</span>
            )
          ) : (
            <span>Despite being {senatorParty === "R" ? "Republican" : "Democrat"}, voting record leans <span className={depth.overallParty === "R" ? "text-red-400" : "text-blue-400"}>{depth.overallParty === "R" ? "Republican" : "Democratic"}</span> overall.</span>
          )}
        </div>
      )}

      {/* Per-policy breakdown */}
      {depth.policyBreakdown.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] text-matrix-green/40 font-pixel mb-1">VOTING RECORD BY POLICY AREA</div>
          {depth.policyBreakdown.map((p, i) => {
            const barWidth = Math.round(p.strength * 100);
            const isR = p.alignment === "R";
            const isD = p.alignment === "D";
            return (
              <div key={i} className="flex items-center gap-2 text-[11px]">
                <span className="w-24 text-matrix-green/60 truncate">
                  <PolicyLabel area={p.area} />
                </span>
                <div className="flex-1 h-2 bg-matrix-green/5 border border-matrix-green/10 relative">
                  <div
                    className={`absolute top-0 bottom-0 ${isR ? "bg-red-500/40 right-0" : isD ? "bg-blue-500/40 left-0" : "bg-purple-500/30 left-1/2 -translate-x-1/2"}`}
                    style={{ width: `${Math.max(barWidth, 4)}%` }}
                  />
                </div>
                <span className={`w-5 text-[10px] font-pixel ${isR ? "text-red-400" : isD ? "text-blue-400" : "text-purple-400"}`}>
                  {p.alignment === "bipartisan" ? "BP" : p.alignment}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function PlatformTracker({ platformSummary, partisanDepth, senatorParty }: PlatformTrackerProps) {
  const hasPartisan = partisanDepth && partisanDepth.totalPositions > 0;
  if (!hasPartisan && !platformSummary) return null;

  const summaryParts: string[] = [];
  if (hasPartisan) {
    summaryParts.push(partisanDepth.depth.toUpperCase());
  }

  return (
    <CollapsibleSection
      title="POSITIONS vs. VOTES"
      summary={summaryParts.join(" — ")}
      source="Derived from roll-call votes"
    >
      {hasPartisan && (
        <PartisanDepthPanel depth={partisanDepth} senatorParty={senatorParty} />
      )}
      {platformSummary && (
        <div className="terminal-window p-3">
          <p className="text-sm text-matrix-green/80 leading-relaxed">{platformSummary}</p>
        </div>
      )}
    </CollapsibleSection>
  );
}
