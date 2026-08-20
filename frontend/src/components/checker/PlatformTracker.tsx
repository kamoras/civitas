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
  deep: { text: "text-signal-magenta", label: "DEEPLY PARTISAN" },
  moderate: { text: "text-signal-amber", label: "MODERATELY PARTISAN" },
  centrist: { text: "text-ink-hi", label: "CENTRIST" },
  "cross-cutting": { text: "text-signal-cyan", label: "CROSS-CUTTING" },
};

function PolicyLabel({ area }: { area: string }) {
  const label = usePolicyLabel(area);
  return <>{label}</>;
}

function PartisanDepthPanel({
  depth,
  senatorParty,
}: {
  depth: PartisanDepth;
  senatorParty: string;
}) {
  const depthStyle = DEPTH_STYLES[depth.depth];
  const leanPct = Math.min((Math.abs(depth.overallLean) / 0.15) * 100, 100);
  const leanDirection = depth.overallLean > 0 ? "R" : depth.overallLean < 0 ? "D" : "center";

  const matchesParty = depth.overallParty === senatorParty;
  const oppositeParty = senatorParty === "R" ? "D" : "R";

  return (
    <div className="panel p-4 mb-4">
      <div className="flex items-baseline justify-between mb-3">
        <h4 className="text-sm font-mono text-signal-cyan">
          {">"}{" "}
          <MetricTooltip text="Measures how partisan this senator's actual votes are. Analyzes roll-call votes on bills and compares them against each party's platform positions. Based on what they voted for, not what they say.">
            PARTISAN DEPTH ANALYSIS
          </MetricTooltip>
        </h4>
        <span className={`text-xs font-mono ${depthStyle.text}`}>{depthStyle.label}</span>
      </div>

      {/* Spectrum bar */}
      <div className="mb-3">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-dem-blue font-display font-semibold">← DEM</span>
          <span className="text-ink-min font-display font-semibold">CENTER</span>
          <span className="text-signal-red font-display font-semibold">REP →</span>
        </div>
        {/* Both party bars paint at full token strength. The D side used to be
            `bg-blue-500/40` against a solid `bg-signal-red`, so identical
            magnitudes rendered as a heavier bar on the Republican side of a
            chart whose only job is to compare the two. */}
        <div className="relative h-3 bg-white/[0.03] border border-white/[0.07]">
          <div className="absolute top-0 bottom-0 left-1/2 w-px bg-phos" />
          <div
            className={`absolute top-0 bottom-0 ${leanDirection === "R" ? "bg-signal-red" : "bg-dem-blue"}`}
            style={{
              left: leanDirection === "R" ? "50%" : `${50 - leanPct / 2}%`,
              width: `${leanPct / 2}%`,
            }}
          />
          <div
            className="absolute top-0 bottom-0 w-1 bg-phos"
            style={{
              left: `${50 + (depth.overallLean / 0.15) * 50}%`,
              transform: "translateX(-50%)",
            }}
          />
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 mb-3 text-center">
        <div className="panel p-2 min-w-0">
          <div
            className={`text-sm font-mono ${depth.overallParty === "R" ? "text-signal-red" : depth.overallParty === "D" ? "text-dem-blue" : "text-ink-hi"}`}
          >
            {depth.overallParty === "centrist" ? "CTR" : depth.overallParty}
          </div>
          <div className="text-xs text-ink-min">
            <MetricTooltip text="Overall ideological direction derived from roll-call votes. R = votes lean Republican, D = votes lean Democrat, CTR = centrist.">
              LEAN
            </MetricTooltip>
          </div>
        </div>
        <div className="panel p-2 min-w-0">
          <div className="text-sm font-mono text-ink-hi">{depth.totalPositions}</div>
          <div className="text-xs text-ink-min">
            <MetricTooltip text="Number of policy areas where this senator has cast votes. Each area's lean is derived from how they voted on D-leaning vs R-leaning bills in that area.">
              AREAS
            </MetricTooltip>
          </div>
        </div>
        <div className="panel p-2 min-w-0">
          <div
            className={`text-sm font-mono ${depth.crossPartyCount > 0 ? "text-signal-cyan" : "text-ink-min"}`}
          >
            {depth.crossPartyCount}
          </div>
          <div className="text-xs text-ink-min">
            <MetricTooltip text="Number of policy areas where this senator's votes align with the opposite party's platform. Higher = more ideologically independent.">
              CROSS
            </MetricTooltip>
          </div>
        </div>
      </div>

      {/* Party alignment interpretation */}
      {senatorParty !== "I" && (
        <div className="text-xs text-ink mb-3">
          {matchesParty ? (
            depth.depth === "deep" ? (
              <span>
                Voting record is <span className={depthStyle.text}>strongly aligned</span> with{" "}
                {senatorParty === "R" ? "Republican" : "Democratic"} positions.
              </span>
            ) : depth.depth === "cross-cutting" ? (
              <span>
                Despite being {senatorParty === "R" ? "Republican" : "Democrat"}, this senator voted
                with {oppositeParty === "R" ? "Republicans" : "Democrats"} in{" "}
                <span className="text-signal-cyan">{depth.crossPartyCount} policy areas</span>.
              </span>
            ) : (
              <span>
                Voting record shows <span className={depthStyle.text}>{depth.depth}</span> alignment
                with {senatorParty === "R" ? "Republican" : "Democratic"} positions.
              </span>
            )
          ) : (
            <span>
              Despite being {senatorParty === "R" ? "Republican" : "Democrat"}, voting record leans{" "}
              <span className={depth.overallParty === "R" ? "text-signal-red" : "text-dem-blue"}>
                {depth.overallParty === "R" ? "Republican" : "Democratic"}
              </span>{" "}
              overall.
            </span>
          )}
        </div>
      )}

      {/* Per-policy breakdown */}
      {depth.policyBreakdown.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs text-ink-min font-mono mb-1">VOTING RECORD BY POLICY AREA</div>
          {depth.policyBreakdown.map((p, i) => {
            const barWidth = Math.round(p.strength * 100);
            const isR = p.alignment === "R";
            const isD = p.alignment === "D";
            return (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="w-24 text-ink-lo truncate">
                  <PolicyLabel area={p.area} />
                </span>
                <div className="flex-1 h-2 bg-white/[0.03] border border-white/[0.07] relative">
                  <div
                    className={`absolute top-0 bottom-0 ${isR ? "bg-signal-red right-0" : isD ? "bg-dem-blue left-0" : "bg-ind-purple left-1/2 -translate-x-1/2"}`}
                    style={{ width: `${Math.max(barWidth, 4)}%` }}
                  />
                </div>
                <span
                  className={`w-5 text-xs font-mono ${isR ? "text-signal-red" : isD ? "text-dem-blue" : "text-ind-purple"}`}
                >
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

export default function PlatformTracker({
  platformSummary,
  partisanDepth,
  senatorParty,
}: PlatformTrackerProps) {
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
      {hasPartisan && <PartisanDepthPanel depth={partisanDepth} senatorParty={senatorParty} />}
      {platformSummary && (
        <div className="panel p-3">
          <p className="text-base text-ink leading-relaxed">{platformSummary}</p>
        </div>
      )}
    </CollapsibleSection>
  );
}
