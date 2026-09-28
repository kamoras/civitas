import type { LobbyingMatch } from "@/types/senator";

/** Distinct bills named in lobbying filings under donors' names, across all
 * of a member's donor-vote matches: a bill named by several clients' filings
 * is one bill, not several. */
export function countLobbiedBills(matches: LobbyingMatch[]): number {
  return new Set(matches.flatMap((m) => (m.lobbiedBills ?? []).map((b) => b.billId))).size;
}
