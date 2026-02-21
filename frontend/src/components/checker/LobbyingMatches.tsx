import { LobbyingMatch } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";

interface LobbyingMatchesProps {
  matches: LobbyingMatch[];
}

export default function LobbyingMatches({ matches }: LobbyingMatchesProps) {
  if (!matches || matches.length === 0) return null;

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-lg text-neon-pink neon-pink">{">"} DONOR-VOTE CONNECTIONS</h3>
        <span className="text-[10px] text-matrix-green/25">fec.gov/data</span>
      </div>

      <div className="space-y-4">
        {matches.map((match, i) => (
          <div key={i} className="terminal-window p-4 border-l-2 border-l-neon-cyan/30">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-neon-cyan text-sm font-bold">{match.lobbyistOrg}</span>
              <span className="text-[10px] px-1.5 py-0.5 border border-matrix-green/20 text-matrix-green/40">
                {match.industry.replace(/_/g, " ")}
              </span>
            </div>

            <div className="text-xs font-mono text-matrix-green/60 mb-3 space-y-1">
              <div>DONATED: {formatCurrency(match.donationToSenator)}</div>
              <div>LINKED BILL: {match.billsInfluenced.join(", ")}</div>
              <div>
                VOTED IN DONOR&apos;S INTEREST:{" "}
                <span
                  className={
                    match.senatorVoteAligned
                      ? "text-neon-pink font-bold"
                      : "text-matrix-green/80"
                  }
                >
                  {match.senatorVoteAligned ? "YES" : "NO"}
                </span>
              </div>
            </div>

            <p className="text-sm text-matrix-green/70">{match.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
