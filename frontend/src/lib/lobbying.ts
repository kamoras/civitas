import type { LobbyingMatch } from "@/types/senator";

/** Distinct bills named in the donors' own lobbying filings, across all of a
 * member's donor-vote matches: a bill two organizations' filings name is one
 * bill, not two. */
export function countLobbiedBills(matches: LobbyingMatch[]): number {
  return new Set(matches.flatMap((m) => (m.lobbiedBills ?? []).map((b) => b.billId))).size;
}
