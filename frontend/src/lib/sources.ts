/**
 * Generate source URLs for government data references.
 * All links point to official .gov domains or the Federal Register.
 */

const CURRENT_CONGRESS = 119;

/**
 * Ordinal form ("119th", "101st", "112th") — congress.gov bill URLs embed
 * it, and a wrong suffix (e.g. "101th") is a dead link.
 */
function congressOrdinal(congress: number): string {
  const mod100 = congress % 100;
  const suffix =
    mod100 >= 11 && mod100 <= 13
      ? "th"
      : { 1: "st", 2: "nd", 3: "rd" }[congress % 10] ?? "th";
  return `${congress}${suffix}`;
}

/**
 * "119th Congress (2025–2026)" — scores are windowed to the current
 * congress only (see AGENTS.md "current term"); this labels that window
 * on the scorecard so a sparser score isn't read as a bug.
 */
export function currentCongressLabel(): string {
  const firstYear = 1789 + (CURRENT_CONGRESS - 1) * 2;
  return `${congressOrdinal(CURRENT_CONGRESS)} Congress (${firstYear}–${firstYear + 1})`;
}

/**
 * Build a congress.gov URL for a bill.
 * Bill IDs come in formats like "HR.5371", "S.1234", "PN373", "HJRES.100"
 * and lobbying bills as "H.R. 7147", "S. 1234".
 *
 * Pass the bill's actual `congress` when the record carries one — a bill
 * ID alone is ambiguous across congresses, and linking a prior-congress
 * bill under the current congress produces a wrong or dead page.
 */
export function billUrl(billId: string, congress?: number | null): string {
  if (!billId) return "";

  const normalized = billId
    .replace(/^H\.R\.\s*/i, "HR.")
    .replace(/^S\.\s*/i, "S.")
    .replace(/^H\.J\.Res\.\s*/i, "HJRES.")
    .replace(/^S\.J\.Res\.\s*/i, "SJRES.")
    .replace(/^H\.Con\.Res\.\s*/i, "HCONRES.")
    .replace(/^S\.Con\.Res\.\s*/i, "SCONRES.")
    .replace(/^H\.Res\.\s*/i, "HRES.")
    .replace(/^S\.Res\.\s*/i, "SRES.");

  const parts = normalized.split(".");
  if (parts.length < 2) return "";

  const typeRaw = parts[0].toUpperCase();
  const number = parts[1].replace(/-.*/, "");

  const typeMap: Record<string, string> = {
    HR: "house-bill",
    S: "senate-bill",
    HJRES: "house-joint-resolution",
    SJRES: "senate-joint-resolution",
    HCONRES: "house-concurrent-resolution",
    SCONRES: "senate-concurrent-resolution",
    HRES: "house-resolution",
    SRES: "senate-resolution",
  };

  const urlType = typeMap[typeRaw];
  if (!urlType) return "";

  const effectiveCongress = congress && congress > 0 ? congress : CURRENT_CONGRESS;
  return `https://www.congress.gov/bill/${congressOrdinal(effectiveCongress)}-congress/${urlType}/${number}`;
}

/**
 * Build a roll call vote URL on senate.gov.
 * billId for roll calls looks like "Roll-119-1-42" (congress-session-rollNumber).
 */
export function rollCallUrl(billId: string): string {
  if (!billId) return "";

  const match = billId.match(/^Roll-(\d+)-(\d+)-(\d+)$/);
  if (!match) return "";

  const [, congress, session, rollNumber] = match;
  return `https://www.senate.gov/legislative/LIS/roll_call_votes/vote${congress}${session}/vote_${congress}_${session}_${rollNumber.padStart(5, "0")}.htm`;
}

/**
 * Link to FEC committee/PAC search by name.
 */
export function fecCommitteeSearchUrl(name: string): string {
  return `https://www.fec.gov/data/committees/?search=${encodeURIComponent(name)}`;
}

/**
 * Build a source link for a vote (bill or roll call).
 */
export function voteSourceUrl(billId: string): string {
  if (billId.startsWith("Roll-")) return rollCallUrl(billId);
  return billUrl(billId);
}
