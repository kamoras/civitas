import Link from "next/link";
import type { RaceSummary } from "@/types/election";

/** Formats a signed PVI int as "R+3"/"D+3"/"EVEN" — display-only, not a computation. */
export function formatPvi(pvi: number | null): string {
  if (pvi == null) return "N/A";
  if (pvi === 0) return "EVEN";
  return pvi > 0 ? `R+${pvi}` : `D+${Math.abs(pvi)}`;
}

export function pviColor(pvi: number | null): string {
  if (pvi == null) return "text-matrix-green/40";
  if (pvi === 0) return "text-white/60";
  return pvi > 0 ? "text-rep-red" : "text-dem-blue";
}

function raceLabel(race: RaceSummary): string {
  if (race.office === "S") return `${race.state} SENATE`;
  return race.district ? `${race.state}-${race.district}` : `${race.state} HOUSE`;
}

export default function RaceCard({ race }: { race: RaceSummary }) {
  return (
    <Link
      href={`/elections/${race.id}`}
      className="block border border-matrix-green/20 bg-terminal-bg/50 p-4 hover:border-neon-cyan/40 transition-colors"
    >
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-pixel text-sm text-white/90">{raceLabel(race)}</h3>
        {race.isSpecial && (
          <span className="text-[9px] font-pixel px-1.5 py-0.5 border border-neon-yellow/30 text-neon-yellow/80">
            SPECIAL
          </span>
        )}
      </div>
      <div className="flex items-center justify-between text-[10px] font-pixel">
        <span className={pviColor(race.pvi)}>{formatPvi(race.pvi)}</span>
        <span className="text-matrix-green/40">
          {race.candidateCount} {race.candidateCount === 1 ? "CANDIDATE" : "CANDIDATES"}
        </span>
      </div>
      {race.topCandidates.length > 0 && (
        <p className="text-xs text-matrix-green/60 mt-2 truncate">
          {race.topCandidates.map((c) => c.name).join(" vs. ")}
        </p>
      )}
    </Link>
  );
}
