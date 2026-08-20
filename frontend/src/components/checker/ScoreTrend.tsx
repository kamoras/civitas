"use client";

import type { ScoreSnapshot } from "@/lib/api";

interface ScoreTrendProps {
  snapshots: ScoreSnapshot[];
}

/* SVG paints through `stroke`/`fill` attributes and an inline `style`, none of
   which a Tailwind class reaches, so the palette has to be named in JS here.
   These mirror tailwind.config.ts — the ratios live there. Before this they
   were a set of hand-written hexes (#ff5555, #00e5ff, #888, #333, #ffaa00)
   that predate the token scales and appear nowhere else on the site. */
const PHOS = "#00FF41";
const SIGNAL_RED = "#FF5C5C";
const SIGNAL_CYAN = "#4DE3E8";
const SIGNAL_ORANGE = "#FF8A3D";
const INK_MIN = "#7C8B7F";
const HAIRLINE = "#2A2F2B";

function congressForDate(dateStr: string): number {
  const year = parseInt(dateStr.slice(0, 4), 10);
  return Math.floor((year - 1789) / 2) + 1;
}

function ordinal(n: number): string {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
  switch (n % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
}

export default function ScoreTrend({ snapshots }: ScoreTrendProps) {
  if (snapshots.length < 2) return null;

  const scores = snapshots.map((s) => s.overallScore);
  const first = scores[0];
  const last = scores[scores.length - 1];
  const change = Math.round(last - first);
  const minScore = Math.max(0, Math.min(...scores) - 5);
  const maxScore = Math.min(100, Math.max(...scores) + 5);
  const range = maxScore - minScore || 1;

  const W = 200;
  const H = 40;
  const PAD = 4;
  const innerW = W - PAD * 2;
  const innerH = H - PAD * 2;

  const toX = (i: number) => PAD + (i / (scores.length - 1)) * innerW;
  const toY = (v: number) => PAD + innerH - ((v - minScore) / range) * innerH;

  const points = scores.map((v, i) => `${toX(i)},${toY(v)}`).join(" ");
  const midY = toY(50);

  // Methodology-change markers: indices where the scoring algorithm
  // version differs from the previous snapshot. A jump at one of these
  // is a formula change, not a behavior change.
  const versionChanges: { i: number; version: string }[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const prev = snapshots[i - 1].algorithmVersion ?? null;
    const cur = snapshots[i].algorithmVersion ?? null;
    if (cur && cur !== prev) {
      versionChanges.push({ i, version: cur });
    }
  }

  // Congress-boundary markers: scores are windowed to the current congress
  // only (see AGENTS.md "current term"), so a jump at a new-congress
  // boundary reflects the score resetting to a fresh 2-year window, not a
  // behavior change. congressForDate mirrors the backend's
  // congress_first_year formula (1st Congress convened 1789) — a fixed
  // historical fact, not a value that needs to be kept in sync by hand.
  const congressChanges: { i: number; congress: number }[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const prevCongress = congressForDate(snapshots[i - 1].date);
    const curCongress = congressForDate(snapshots[i].date);
    if (curCongress !== prevCongress) {
      congressChanges.push({ i, congress: curCongress });
    }
  }

  const changeColor = change > 0 ? PHOS : change < 0 ? SIGNAL_RED : INK_MIN;
  const changeLabel = change > 0 ? `↑ +${change}` : change < 0 ? `↓ ${change}` : "→ 0";

  return (
    <div
      className="mt-2"
      aria-label={`Score trend from ${snapshots[0].date} to ${snapshots[snapshots.length - 1].date}: ${first} to ${last}`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-xs text-ink-lo tracking-widest">SCORE HISTORY</span>
        <span className="font-mono text-xs" style={{ color: changeColor }}>
          {changeLabel} since first snapshot
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: "40px" }}
        aria-hidden="true"
      >
        {/* Reference line at 50 */}
        <line
          x1={PAD}
          y1={midY}
          x2={W - PAD}
          y2={midY}
          stroke={HAIRLINE}
          strokeWidth="0.5"
          strokeDasharray="3,3"
        />
        {/* Methodology-change markers */}
        {versionChanges.map(({ i, version }) => (
          <line
            key={`v-${version}-${i}`}
            x1={toX(i)}
            y1={PAD}
            x2={toX(i)}
            y2={H - PAD}
            stroke={SIGNAL_CYAN}
            strokeWidth="0.75"
            strokeDasharray="2,2"
            opacity="0.6"
          />
        ))}
        {/* Congress-boundary markers */}
        {congressChanges.map(({ i, congress }) => (
          <line
            key={`c-${congress}-${i}`}
            x1={toX(i)}
            y1={PAD}
            x2={toX(i)}
            y2={H - PAD}
            stroke={SIGNAL_ORANGE}
            strokeWidth="0.75"
            strokeDasharray="4,2"
            opacity="0.6"
          />
        ))}
        {/* Trend line */}
        <polyline points={points} stroke={PHOS} strokeWidth="1.5" fill="none" opacity="0.8" />
        {/* First point */}
        <circle cx={toX(0)} cy={toY(first)} r="2.5" fill={PHOS} />
        {/* Last point */}
        <circle cx={toX(scores.length - 1)} cy={toY(last)} r="2.5" fill={PHOS} />
      </svg>
      {/* The endpoint scores read here rather than as <text> inside the svg.
          `preserveAspectRatio="none"` is what lets the 200-unit viewBox stretch
          to the panel width, and it stretches glyphs with everything else — at
          a typical ~845px render that is 4.2x horizontally against 1x
          vertically, which smeared both numbers into the trend line. Nothing
          about them needs to be positioned in chart space. */}
      <div className="flex justify-between text-xs text-ink-min font-mono mt-0.5">
        <span>
          {snapshots[0].date} · <span className="text-phos-mid">{first}</span>
        </span>
        <span>
          <span className="text-phos-mid">{last}</span> · {snapshots[snapshots.length - 1].date}
        </span>
      </div>
      {versionChanges.length > 0 && (
        <div className="text-xs text-ink-lo font-mono mt-0.5">
          ┊ methodology updated ({versionChanges.map((v) => v.version).join(", ")}) — see{" "}
          <a href="/changelog" className="underline underline-offset-2 hover:text-phos">
            scoring changelog
          </a>
        </div>
      )}
      {congressChanges.length > 0 && (
        <div className="text-xs text-signal-orange font-mono mt-0.5">
          ┊ {congressChanges.map((c) => `${ordinal(c.congress)} Congress`).join(", ")} began —
          scores reset to reflect the new term
        </div>
      )}
    </div>
  );
}
