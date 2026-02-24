/**
 * Generate source URLs for government data references.
 * All links point to official .gov domains or the Federal Register.
 */

const CURRENT_CONGRESS = 119;

/**
 * Build a congress.gov URL for a bill.
 * Bill IDs come in formats like "HR.5371", "S.1234", "PN373", "HJRES.100"
 * and lobbying bills as "H.R. 7147", "S. 1234".
 */
export function billUrl(billId: string): string {
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

  return `https://www.congress.gov/bill/${CURRENT_CONGRESS}th-congress/${urlType}/${number}`;
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
 * Link to the Senate contact page.
 */
export function senateGovUrl(): string {
  return `https://www.senate.gov/senators/senators-contact.htm`;
}

/**
 * Link to the Congressional Record daily edition on GovInfo.
 */
export function congressionalRecordUrl(date: string): string {
  if (!date) return "";
  return `https://www.congress.gov/congressional-record/${date.replace(/-/g, "/")}`;
}

/**
 * Build a source link for a vote (bill or roll call).
 */
export function voteSourceUrl(billId: string): string {
  if (billId.startsWith("Roll-")) return rollCallUrl(billId);
  return billUrl(billId);
}

/**
 * Federal Register search for presidential documents.
 */
export function federalRegisterPresidentUrl(): string {
  return `https://www.federalregister.gov/presidential-documents`;
}

/**
 * BLS employment data page.
 */
export function blsEmploymentUrl(): string {
  return "https://data.bls.gov/timeseries/CES0000000001";
}
