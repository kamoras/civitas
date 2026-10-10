import type { PoliticianCard } from "@/types/politicians";

const BRANCH_ORDER: Record<string, number> = { president: 0, senate: 1, house: 2, scotus: 3 };

/** One state's delegation the way its voters look for it: senators, then
 * House members by district number (an at-large seat is 0), ties by name.
 * The unfiltered directory keeps the API's alphabetical order. */
export function delegationOrder(list: PoliticianCard[]): PoliticianCard[] {
  return [...list].sort(
    (a, b) =>
      (BRANCH_ORDER[a.branch] ?? 9) - (BRANCH_ORDER[b.branch] ?? 9) ||
      (a.district ?? 0) - (b.district ?? 0) ||
      a.name.localeCompare(b.name)
  );
}
