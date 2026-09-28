import { LobbyingMatch } from "@/types/senator";
import { formatCurrency, safeHref } from "@/lib/formatting";
import { billUrl } from "@/lib/sources";

interface LobbyingMatchesProps {
  matches: LobbyingMatch[];
}

// What the vote shown decided, when it wasn't the vote on the bill itself
// (bill_learning.MOTION_PROTOTYPES). Neutral on purpose: "veto" also covers
// motions to refer or table a veto message, where a Yea is not an override.
const MOTION_LABELS: Record<string, string> = {
  amendment: " (on an amendment to it)",
  cloture: " (on a cloture motion)",
  procedural: " (on a procedural motion)",
  veto: " (on a motion about the President's veto)",
  nomination: " (on a nomination)",
};

function motionLabel(motionType: string | null | undefined): string {
  if (motionType === "passage") return "";
  // Unrecognized, or no question recorded: don't let it read as passage.
  return (motionType && MOTION_LABELS[motionType]) || " (on a motion, not necessarily passage)";
}

export default function LobbyingMatches({ matches }: LobbyingMatchesProps) {
  if (!matches || matches.length === 0) return null;

  return (
    <div>
      <div className="text-xs text-ink-lo mb-3">
        Industries that make up a large share of this member&apos;s classifiable donor money,
        matched to votes on legislation in that industry&apos;s policy area. Lobbying Disclosure
        Act reports are looked up under the largest donor&apos;s name; where a report names a bill
        the member voted on, the filing is linked with the client it was filed for, which can be a
        separate company sharing the name. A filing records lobbying on a bill, not which way;
        none of this shows that money changed a vote.
      </div>
      <div className="space-y-4">
        {matches.map((match, i) => {
          const lobbied = match.lobbiedBills ?? [];
          // The backend keeps billsInfluenced to bills no filing names.
          const topical = match.billsInfluenced;
          return (
            <div key={i} className="panel p-4 border-l-2 border-l-signal-cyan/40">
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <span className="text-signal-cyan text-sm font-bold">{match.lobbyistOrg}</span>
                <span className="text-xs px-1.5 py-0.5 border border-white/[0.07] text-ink-min">
                  {match.industry.replace(/_/g, " ")}
                </span>
              </div>

              <div className="text-xs font-mono text-ink-lo mb-3 space-y-1">
                <div>ASSOCIATED CONTRIBUTIONS: {formatCurrency(match.donationToSenator)}</div>
                {match.lobbyingChecked === false && (
                  <div>LOBBYING REGISTRY: lookup failed on the last run, spend unknown</div>
                )}
                {lobbied.length > 0 && (
                  <div>
                    <div>NAMED IN LOBBYING FILINGS FOR CLIENTS OF THIS NAME:</div>
                    <ul className="mt-1 space-y-1 pl-3">
                      {lobbied.map((b) => {
                        const bill = billUrl(b.billId);
                        const filing = b.filingUrl ? safeHref(b.filingUrl) : null;
                        return (
                          <li key={b.billId}>
                            {bill ? (
                              <a
                                href={safeHref(bill) || "#"}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-ink hover:text-phos underline underline-offset-2 transition-colors"
                              >
                                {b.label || b.billId}
                              </a>
                            ) : (
                              <span className="text-ink">{b.label || b.billId}</span>
                            )}
                            {b.vote && (
                              <>
                                {" "}
                                · voted {b.vote}
                                {motionLabel(b.motionType)}
                              </>
                            )}
                            {filing && (
                              <>
                                {" · "}
                                <a
                                  href={filing}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="hover:text-phos underline underline-offset-2 transition-colors"
                                >
                                  {b.filingYear ? `${b.filingYear} filing` : "filing"}
                                  {b.client ? ` for ${b.client}` : ""}
                                  {b.registrant && b.registrant !== b.client ? ` by ${b.registrant}` : ""}
                                </a>
                                {b.filingCount > 1 && ` (+${b.filingCount - 1} more)`}
                              </>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
                {topical.length > 0 && (
                  <div className="flex items-center gap-1 flex-wrap">
                    <span>TOPICALLY RELATED BILLS:</span>
                    {topical.map((b, j) => {
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
                )}
              </div>

              <p className="text-base text-ink">{match.description}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
