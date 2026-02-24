import { CampaignPromise } from "@/types/senator";
import { useCategoryLabel } from "@/hooks/useConfig";
import { voteSourceUrl } from "@/lib/sources";

interface PlatformTrackerProps {
  promises: CampaignPromise[];
  platformSummary: string;
}

const ALIGNMENT_STYLES = {
  kept: {
    label: "KEPT",
    text: "text-matrix-green",
    border: "border-matrix-green/30",
    bg: "bg-matrix-green/10",
    icon: "[✓]",
  },
  broken: {
    label: "BROKEN",
    text: "text-neon-pink",
    border: "border-neon-pink/40",
    bg: "bg-neon-pink/10",
    icon: "[✗]",
  },
  partial: {
    label: "PARTIAL",
    text: "text-yellow-500",
    border: "border-yellow-500/30",
    bg: "bg-yellow-500/10",
    icon: "[~]",
  },
  unclear: {
    label: "UNCLEAR",
    text: "text-matrix-green/40",
    border: "border-matrix-green/15",
    bg: "bg-matrix-green/5",
    icon: "[?]",
  },
};

function CategoryBadge({ category }: { category: string }) {
  const label = useCategoryLabel(category);
  return (
    <span className="text-[10px] px-1.5 py-0.5 border border-matrix-green/15 text-matrix-green/40">
      {label}
    </span>
  );
}

export default function PlatformTracker({ promises, platformSummary }: PlatformTrackerProps) {
  if (promises.length === 0) return null;

  const kept = promises.filter((p) => p.alignment === "kept").length;
  const broken = promises.filter((p) => p.alignment === "broken").length;
  const partial = promises.filter((p) => p.alignment === "partial").length;

  // Sort: broken first (most interesting), then partial, then kept, then unclear
  const sortOrder = { broken: 0, partial: 1, kept: 2, unclear: 3 };
  const sorted = [...promises].sort(
    (a, b) => sortOrder[a.alignment] - sortOrder[b.alignment],
  );

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-lg text-neon-cyan neon-cyan">{">"} PROMISES vs. VOTES</h3>
        <span className="text-[10px] text-matrix-green/50">
          AI-analyzed campaign platform
        </span>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-2 mb-3 text-center">
        <div className="terminal-window p-2">
          <div className="text-lg font-pixel text-matrix-green">{kept}</div>
          <div className="text-[10px] text-matrix-green/40">KEPT</div>
        </div>
        <div className="terminal-window p-2">
          <div className="text-lg font-pixel text-neon-pink">{broken}</div>
          <div className="text-[10px] text-matrix-green/40">BROKEN</div>
        </div>
        <div className="terminal-window p-2">
          <div className="text-lg font-pixel text-yellow-500">{partial}</div>
          <div className="text-[10px] text-matrix-green/40">PARTIAL</div>
        </div>
      </div>

      {platformSummary && (
        <div className="terminal-window p-3 mb-3">
          <p className="text-sm text-matrix-green/80 leading-relaxed">{platformSummary}</p>
        </div>
      )}

      {/* Promise cards */}
      <div className="space-y-2">
        {sorted.map((promise, i) => {
          const style = ALIGNMENT_STYLES[promise.alignment];
          return (
            <div key={i} className={`terminal-window border-l-2 ${style.border}`}>
              <div className="p-3 space-y-2">
                <div className="flex items-start gap-2">
                  <span className={`font-pixel text-xs ${style.text} shrink-0`}>
                    {style.icon}
                  </span>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={`text-[10px] px-1.5 py-0.5 border font-pixel ${style.text} ${style.border} ${style.bg}`}
                      >
                        {style.label}
                      </span>
                      <CategoryBadge category={promise.category} />
                    </div>
                    <p className="text-sm text-matrix-green/90 mt-1">{promise.promiseText}</p>
                    {promise.analysis && (
                      <div className="mt-2 border-l-2 border-matrix-green/20 pl-2">
                        <span className="text-[10px] text-matrix-green/40 font-pixel">EVIDENCE: </span>
                        <span className="text-xs text-matrix-green/70">{promise.analysis}</span>
                      </div>
                    )}
                    {promise.relatedVotes && promise.relatedVotes.length > 0 && (
                      <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px] text-matrix-green/30">RELATED VOTES:</span>
                        {promise.relatedVotes.map((v, j) => {
                          const url = voteSourceUrl(v);
                          return url ? (
                            <a
                              key={j}
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[10px] text-neon-cyan/50 hover:text-neon-cyan underline underline-offset-2 transition-colors"
                            >
                              {v}
                            </a>
                          ) : (
                            <span key={j} className="text-[10px] text-matrix-green/40">{v}</span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
