/** Presentation helpers for the Congress reports. Counts, sentences and
 * results all come from the API; this only formats dates and builds links. */

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "2026-09-24" as a calendar date, never shifted by the reader's time zone. */
function parts(iso: string): { y: number; m: number; d: number; weekday: number } {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m, d, weekday: new Date(Date.UTC(y, m - 1, d)).getUTCDay() };
}

/** "Thursday, September 24, 2026" */
export function longDate(iso: string): string {
  const { y, m, d, weekday } = parts(iso);
  return `${WEEKDAYS[weekday]}, ${MONTHS[m - 1]} ${d}, ${y}`;
}

/** "Thu, Sep 24" */
export function shortDate(iso: string): string {
  const { m, d, weekday } = parts(iso);
  return `${WEEKDAYS[weekday].slice(0, 3)}, ${MONTHS[m - 1].slice(0, 3)} ${d}`;
}

/** "September 2026" from "2026-09" */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

export function dayHref(iso: string): string {
  return `/congress/${iso}`;
}

export function weekHref(iso: string): string {
  return `/congress/week/${iso}`;
}

export function monthHref(iso: string): string {
  return `/congress/month/${iso.slice(0, 7)}`;
}

export function billHref(billId: string): string {
  return `/congress/bills/${encodeURIComponent(billId)}`;
}

/** The chamber's own result, as the site colours it: carried, rejected, or
 * neither said (a quorum call). Never derived from the tally. */
export function resultTone(rejected: boolean | null): "yes" | "no" | "neutral" {
  if (rejected === false) return "yes";
  if (rejected === true) return "no";
  return "neutral";
}

export const CHAMBER_NAME = { senate: "Senate", house: "House" } as const;
