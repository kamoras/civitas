import { placeName, searchFold } from "@/lib/elections";
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

/** What the finder offers as you type: every county, then every city,
 *  town and community by the name people use ("Brooklyn", not "Brooklyn
 *  borough"), once each. */
export function finderSuggestions(lines: HouseDistrictsByCounty | null): string[] {
  const places = new Set<string>();
  for (const d of lines?.districts ?? []) for (const p of d.places ?? []) places.add(placeName(p));
  return [...countyNames(lines), ...[...places].sort()];
}

/** What the finder says about a query, in plain words. A county or city
 *  split between districts has more than one member, and the finder must
 *  say so rather than let the first card read as the answer.
 *  `placeLines` is placeDistrictSummary's answer for a city, town or
 *  community typed in full, which speaks for itself. */
export function countyFinderStatus(
  names: string[],
  query: string,
  matchedDistricts: number,
  placeLines: string[] = []
): string | null {
  const q = searchFold(query);
  if (!q) return null;
  if (matchedDistricts === 0) return "No district matches that city, town or county.";
  if (placeLines.length) return placeLines.join(" ");
  const counties = names.filter((n) => searchFold(n).includes(q));
  // Part of a place's name: the cards are the matches, but which place?
  if (counties.length === 0)
    return `${matchedDistricts} ${matchedDistricts === 1 ? "district matches" : "districts match"}. Type the full name of your city, town or county to see which districts it is in.`;
  if (counties.length > 1)
    return `${counties.length} counties match. Keep typing, or pick one from the list.`;
  const county = counties[0] ?? "That county";
  if (matchedDistricts === 1) return `One district covers ${county}.`;
  return `${county} is split between ${matchedDistricts} districts, so it has more than one member. Your ballot or your state's voter lookup shows which one is yours.`;
}
