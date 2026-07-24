import Link from "next/link";
import { formatPvi, pviColor, raceShortLabel } from "@/lib/elections";
import type { RaceSummary } from "@/types/election";

export default function RaceCard({ race }: { race: RaceSummary }) {
  return (
    <Link
      href={`/elections/${race.id}`}
      className="block border border-matrix-green/20 bg-terminal-bg/50 p-4 hover:border-neon-cyan/40 transition-colors"
    >
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-pixel text-sm text-white/90">{raceShortLabel(race)}</h3>
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
        // "·" not "vs." — pre-primary, top-2-by-cash is often not the
        // general-election matchup, so don't imply a head-to-head.
        <p className="text-xs text-matrix-green/60 mt-2 truncate">
          <span className="font-pixel text-[9px] text-matrix-green/40 mr-1">
            LEADING FUNDRAISERS:
          </span>
          {race.topCandidates.map((c) => c.name).join(" · ")}
        </p>
      )}
    </Link>
  );
}
