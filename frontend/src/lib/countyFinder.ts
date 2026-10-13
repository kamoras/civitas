import { searchFold } from "@/lib/elections";
import type { HouseDistrictsByCounty } from "@/types/politicians";

const PART = / \(part\)$/;

/** The county names a state's districts are described by, once each and
 *  without the "(part)" tag (a county split between districts), sorted. */
export function countyNames(lines: HouseDistrictsByCounty | null): string[] {
  const names = new Set<string>();
  for (const d of lines?.districts ?? []) {
    for (const c of d.counties) names.add(c.replace(PART, ""));
  }
  return [...names].sort();
}

/** What the county finder says about a query, in plain words. A county
 *  split between districts has more than one member, and the finder must
 *  say so rather than let the first card read as the answer. */
export function countyFinderStatus(
  names: string[],
  query: string,
  matchedDistricts: number
): string | null {
  const q = searchFold(query);
  if (!q) return null;
  if (matchedDistricts === 0) return "No district matches that county.";
  const counties = names.filter((n) => searchFold(n).includes(q));
  if (counties.length > 1)
    return `${counties.length} counties match. Keep typing, or pick one from the list.`;
  const county = counties[0] ?? "That county";
  if (matchedDistricts === 1) return `One district covers ${county}.`;
  return `${county} is split between ${matchedDistricts} districts, so it has more than one member. Your ballot or your state's voter lookup shows which one is yours.`;
}
