"use client";

import type { ScoreSnapshot } from "@/lib/api";

interface ScoreTrendProps {
  snapshots: ScoreSnapshot[];
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

  const changeColor = change > 0 ? "#00ff41" : change < 0 ? "#ff5555" : "#888";
  const changeLabel = change > 0 ? `↑ +${change}` : change < 0 ? `↓ ${change}` : "→ 0";

  return (
    <div
      className="mt-2"
      aria-label={`Score trend from ${snapshots[0].date} to ${snapshots[snapshots.length - 1].date}: ${first} to ${last}`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-pixel text-[9px] text-neon-cyan/50 tracking-widest">SCORE HISTORY</span>
        <span className="font-pixel text-[9px]" style={{ color: changeColor }}>
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
        <line x1={PAD} y1={midY} x2={W - PAD} y2={midY} stroke="#333" strokeWidth="0.5" strokeDasharray="3,3" />
        {/* Trend line */}
        <polyline points={points} stroke="#00ff41" strokeWidth="1.5" fill="none" opacity="0.8" />
        {/* First point */}
        <circle cx={toX(0)} cy={toY(first)} r="2.5" fill="#00ff41" />
        {/* Last point */}
        <circle cx={toX(scores.length - 1)} cy={toY(last)} r="2.5" fill="#00ff41" />
        {/* First score label */}
        <text
          x={PAD + 2}
          y={toY(first) - 4}
          fontSize="7"
          fill="#00ff41"
          fontFamily="monospace"
          opacity="0.7"
        >
          {first}
        </text>
        {/* Last score label */}
        <text
          x={W - PAD - 2}
          y={toY(last) - 4}
          fontSize="7"
          fill="#00ff41"
          fontFamily="monospace"
          textAnchor="end"
          opacity="0.7"
        >
          {last}
        </text>
      </svg>
      <div className="flex justify-between text-[8px] text-matrix-green/30 font-mono mt-0.5">
        <span>{snapshots[0].date}</span>
        <span>{snapshots[snapshots.length - 1].date}</span>
      </div>
    </div>
  );
}
