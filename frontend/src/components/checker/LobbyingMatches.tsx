import { LobbyingMatch } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";

interface LobbyingMatchesProps {
  matches: LobbyingMatch[];
}

export default function LobbyingMatches({ matches }: LobbyingMatchesProps) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-lg text-neon-pink neon-pink">{">"} LOBBYING MATCHES</h3>
        <span className="text-[10px] text-matrix-green/25">
          Source: lda.senate.gov &amp; fec.gov/data
        </span>
      </div>
      <p className="text-xs text-matrix-green/40 mb-4">
        Side-by-side view of lobbying spend, campaign donations, and voting record from public
        filings. Presented for transparency — draw your own conclusions.
      </p>

      <div className="space-y-4">
        {matches.map((match, i) => (
          <div key={i} className="terminal-window p-4 border-l-2 border-l-neon-cyan/30">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-neon-cyan text-sm font-bold">{match.lobbyistOrg}</span>
            </div>

            <div className="text-xs font-mono text-matrix-green/60 mb-3 space-y-1 break-all">
              <div>LOBBYING SPEND: {formatCurrency(match.lobbyingSpend)}</div>
              <div>DONATION TO SENATOR: {formatCurrency(match.donationToSenator)}</div>
              <div>BILLS TARGETED: {match.billsInfluenced.join(", ")}</div>
              <div>
                SENATOR VOTED WITH LOBBY POSITION:{" "}
                <span className="text-matrix-green/80">
                  {match.senatorVoteAligned ? "YES" : "NO"}
                </span>
              </div>
            </div>

            <p className="text-sm text-matrix-green/70 italic">{match.description}</p>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-matrix-green/30 mt-3 italic">
        A senator voting in line with a lobby position does not necessarily mean the vote was
        influenced by donations. Many factors affect legislative decisions including party platform,
        constituent interests, and policy beliefs.
      </p>
    </div>
  );
}
