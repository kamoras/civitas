import { safeHref } from "@/lib/formatting";
import type { RaceCoverageItem } from "@/types/election";

const RECENT_THRESHOLD_MS = 24 * 60 * 60 * 1000;

function formatItemTime(item: RaceCoverageItem): { timeLabel: string; isRecent: boolean } {
  if (!item.publishedAt) return { timeLabel: "", isRecent: false };
  const published = new Date(item.publishedAt);
  if (isNaN(published.getTime())) return { timeLabel: "", isRecent: false };
  const ageMs = Date.now() - published.getTime();
  return {
    timeLabel: published.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }),
    isRecent: ageMs >= 0 && ageMs < RECENT_THRESHOLD_MS,
  };
}

export default function CoverageFeed({ items }: { items: RaceCoverageItem[] }) {
  if (items.length === 0) {
    return <p className="text-sm text-matrix-green/40">No coverage ingested for this race yet.</p>;
  }

  return (
    <div className="relative pl-4 border-l border-neon-cyan/20 space-y-4" role="list">
      {items.map((item) => {
        const { timeLabel, isRecent } = formatItemTime(item);
        return (
          <div key={item.id} className="relative" role="listitem">
            <div
              className={`absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full border ${
                isRecent
                  ? "bg-neon-cyan border-neon-cyan animate-pulse"
                  : "bg-neon-cyan/30 border-neon-cyan/50"
              }`}
              aria-hidden="true"
            />
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              {timeLabel && (
                <time dateTime={item.publishedAt || undefined} className="text-[10px] text-matrix-green/40 font-pixel">
                  {timeLabel}
                </time>
              )}
              <span className="text-[9px] font-pixel px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/50">
                {item.sourceType === "bluesky" ? "BLUESKY" : "NEWS"}
              </span>
              <span className="text-[10px] text-matrix-green/30 font-pixel">via {item.sourceName}</span>
            </div>
            <p className="text-sm text-matrix-green/80 leading-relaxed mb-1">{item.summary}</p>
            <a
              href={safeHref(item.url) || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] text-neon-cyan/60 hover:text-neon-cyan transition-colors"
            >
              {item.title || "Source"} <span aria-hidden="true">↗</span>
            </a>
          </div>
        );
      })}
    </div>
  );
}
