import { formatUtcDate } from "@/lib/formatting";

/** "Oct 15, 2026" for a YYYY-MM-DD the backend says it read something on. */
export function readOn(iso: string | null): string {
  return iso
    ? formatUtcDate(iso, { year: "numeric", month: "short", day: "numeric" }, "en-US")
    : "not yet";
}

// Fiscal months: the federal fiscal year starts October 1.
const FISCAL_MONTHS = [
  "October",
  "November",
  "December",
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
];

/** "FY2025" for a whole fiscal year, "FY2026 through August" for one whose
 * figures stop short of its twelfth month (USAspending's reporting period). */
export function fiscalYearLabel(fy: number | null, throughPeriod: number | null): string {
  if (!fy) return "";
  return throughPeriod && throughPeriod < 12
    ? `FY${fy} through ${FISCAL_MONTHS[throughPeriod - 1]}`
    : `FY${fy}`;
}
