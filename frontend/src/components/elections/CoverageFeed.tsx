"use client";

import { useSyncExternalStore } from "react";
import { safeHref } from "@/lib/formatting";
import { parseUtc, raceBadgeLabel } from "@/lib/elections";
import type { RaceCoverageItem } from "@/types/election";

const RECENT_THRESHOLD_MS = 24 * 60 * 60 * 1000;

// true after hydration, false during SSR and the first client render —
// the useSyncExternalStore server/client-snapshot idiom (see StancePulse.tsx
// for repo precedent), which avoids a setState-in-effect.
const noopSubscribe = () => () => {};
function useMounted(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false
  );
}

function formatItemTime(item: RaceCoverageItem): { timeLabel: string; isRecent: boolean } {
  if (!item.publishedAt) return { timeLabel: "", isRecent: false };
  const published = parseUtc(item.publishedAt);
  if (!published) return { timeLabel: "", isRecent: false };
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
  // Viewer-local time formatting and the "recent" marker both depend on the
  // viewer's clock/locale, so they must not run during the (cached, 120s)
  // server render — that guaranteed a hydration mismatch. Server render:
  // no time label, non-pulsing dot; real values fill in after mount.
  const mounted = useMounted();

  if (items.length === 0) {
    return (
      <p className="font-mono text-sm text-ink-min">No coverage ingested for this race yet.</p>
    );
  }

  return (
    <ol className="relative space-y-0 border-l border-white/15 pl-5" role="list">
      {items.map((item) => {
        const { timeLabel, isRecent } = mounted
          ? formatItemTime(item)
          : { timeLabel: "", isRecent: false };
        const href = safeHref(item.url);
        const isBluesky = item.sourceType === "bluesky";
        return (
          <li key={item.id} className="relative border-b border-white/[0.07] py-4 last:border-b-0">
            {/* A square tick on the rail, not a rounded dot: the register uses
                hard corners throughout, and a filled tick reads as an entry
                marker on a timeline rather than a status light. */}
            <span
              className={`absolute -left-[23px] top-[22px] h-1.5 w-3 ${
                isRecent ? "bg-phos" : "bg-ink-min"
              }`}
              aria-hidden="true"
            />

            {/* Docket line: when, what kind, from whom — the same shape the
                homepage index uses, so an entry looks the same wherever it
                appears. */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs">
              {timeLabel && (
                <time
                  dateTime={item.publishedAt || undefined}
                  className={isRecent ? "text-phos-mid" : "text-ink-lo"}
                >
                  {timeLabel}
                </time>
              )}
              <span
                className={
                  isBluesky
                    ? "border border-signal-magenta/40 px-1.5 py-0.5 tracking-[0.1em] text-signal-magenta"
                    : "border border-signal-cyan/40 px-1.5 py-0.5 tracking-[0.1em] text-signal-cyan"
                }
              >
                {isBluesky ? "BLUESKY" : "NEWS"}
              </span>
              <span className="text-ink-min">via {item.sourceName}</span>
              {item.race && (
                <span className="border border-white/15 px-1.5 py-0.5 tracking-[0.1em] text-ink-lo">
                  {raceBadgeLabel(item.race)}
                </span>
              )}
              {isRecent && <span className="tracking-[0.1em] text-phos-mid">LAST 24H</span>}
            </div>

            <p className="mt-2 font-display text-base leading-relaxed text-ink">{item.summary}</p>

            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1.5 inline-block border-b border-phos-mid/40 font-mono text-xs text-phos-mid transition-colors hover:text-phos"
              >
                {item.title || "Source"} <span aria-hidden="true">↗</span>
              </a>
            ) : (
              <span className="mt-1.5 inline-block font-mono text-xs text-ink-min">
                {item.title || "Source"}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
