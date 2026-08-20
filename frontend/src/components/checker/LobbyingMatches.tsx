import { LobbyingMatch } from "@/types/senator";
import { formatCurrency, safeHref } from "@/lib/formatting";
import { billUrl } from "@/lib/sources";

interface LobbyingMatchesProps {
  matches: LobbyingMatch[];
}

export default function LobbyingMatches({ matches }: LobbyingMatchesProps) {
  if (!matches || matches.length === 0) return null;

  return (
    <div>
      <div className="text-xs text-ink-lo mb-3">
        Cases where money associated with an organization (employee donations plus PAC
        contributions, aggregated across recent cycles) overlaps topically with legislation the
        senator voted on. Overlap is detected by semantic similarity, not lobbying-registry records,
        and does not prove influence — it highlights where money and votes intersect.
      </div>
      <div className="space-y-4">
        {matches.map((match, i) => (
          <div key={i} className="panel p-4 border-l-2 border-l-signal-cyan/40">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-signal-cyan text-sm font-bold">{match.lobbyistOrg}</span>
              <span className="text-xs px-1.5 py-0.5 border border-white/[0.07] text-ink-min">
                {match.industry.replace(/_/g, " ")}
              </span>
            </div>

            <div className="text-xs font-mono text-ink-lo mb-3 space-y-1">
              <div>ASSOCIATED CONTRIBUTIONS: {formatCurrency(match.donationToSenator)}</div>
              <div className="flex items-center gap-1 flex-wrap">
                <span>TOPICALLY RELATED BILLS:</span>
                {match.billsInfluenced.map((b, j) => {
                  const url = billUrl(b);
                  return url ? (
                    <a
                      key={j}
                      href={safeHref(url) || "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-ink-lo hover:text-phos underline underline-offset-2 transition-colors"
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
                      match.senatorVoteAligned ? "text-signal-magenta font-bold" : "text-ink"
                    }
                  >
                    {match.senatorVoteAligned ? "YES" : "NO"}
                  </span>
                </div>
              )}
            </div>

            <p className="text-base text-ink">{match.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
