/**
 * Generate source URLs for government data references.
 * All links point to official .gov domains or the Federal Register.
 */

/**
 * The Congress currently in session, derived from the wall clock — a new
 * Congress convenes Jan 3 of each odd year (off by one for ~2 days before
 * that in an odd January, same as the backend's congress_for_year). Computed
 * rather than hardcoded so this label/fallback never needs a manual bump.
 */
function currentCongress(): number {
  return 1 + Math.floor((new Date().getFullYear() - 1789) / 2);
}

/**
 * Ordinal form ("119th", "101st", "112th").
 */
function congressOrdinal(congress: number): string {
  const mod100 = congress % 100;
  const suffix =
    mod100 >= 11 && mod100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[congress % 10] ?? "th");
  return `${congress}${suffix}`;
}

/**
 * "119th Congress (2025–2026)" — scores are windowed to the current
 * congress only (see AGENTS.md "current term"); this labels that window
 * on the scorecard so a sparser score isn't read as a bug.
 */
export function currentCongressLabel(): string {
  const congress = currentCongress();
  const firstYear = 1789 + (congress - 1) * 2;
  return `${congressOrdinal(congress)} Congress (${firstYear}–${firstYear + 1})`;
}

/**
 * Link to FEC committee/PAC search by name.
 */
export function fecCommitteeSearchUrl(name: string): string {
  return `https://www.fec.gov/data/committees/?search=${encodeURIComponent(name)}`;
}
