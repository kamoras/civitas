import { LobbyingMatch } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";
import { billUrl } from "@/lib/sources";

interface LobbyingMatchesProps {
  matches: LobbyingMatch[];
}

export default function LobbyingMatches({ matches }: LobbyingMatchesProps) {
  if (!matches || matches.length === 0) return null;

  return (
    <div>
      <div className="text-[10px] text-matrix-green/50 mb-3">
        Cases where a donor to this senator also had interests in legislation the senator voted on.
        A connection does not prove influence — it highlights where money and votes intersect.
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
              <div className="flex items-center gap-1 flex-wrap">
                <span>LINKED BILL:</span>
                {match.billsInfluenced.map((b, j) => {
                  const url = billUrl(b);
                  return url ? (
                    <a
                      key={j}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-neon-cyan/60 hover:text-neon-cyan underline underline-offset-2 transition-colors"
                    >
                      {b}
                    </a>
                  ) : (
                    <span key={j}>{b}</span>
                  );
                })}
              </div>
              {match.senatorVoteAligned !== null && match.senatorVoteAligned !== undefined && (
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
              )}
            </div>

            <p className="text-sm text-matrix-green/70">{match.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
