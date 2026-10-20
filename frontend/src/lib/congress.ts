/** Presentation helpers for the Congress reports. Counts, sentences and
 * results all come from the API; this only formats dates and builds links. */

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
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

/** "September 24, 2026": a date inside a sentence. Anything not ISO is
 * returned as given. */
export function calendarDate(iso: string): string {
  if (!/^\d{4}-\d{2}-\d{2}/.test(iso)) return iso;
  const { y, m, d } = parts(iso);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

/** "Thu, Sep 24" */
export function shortDate(iso: string): string {
  // A vote stored before dates were ISO ("October 14, 2025, 05:34 PM") is
  // shown as stored until the next run rewrites it.
  if (!/^\d{4}-\d{2}-\d{2}/.test(iso)) return iso;
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

/** The site's page for a bill. A bill number names a different bill in each
 * Congress ("S. 1" of the 118th is not the 119th's), so a link that knows
 * which Congress it means says so; without one the page shows the current
 * Congress's bill of that number. */
export function billHref(billId: string, congress?: number | null): string {
  const path = `/congress/bills/${encodeURIComponent(billId)}`;
  return congress ? `${path}?congress=${congress}` : path;
}

/** The bills list holding this Congress's bills that got at least as far
 * as a milestone (its stage code): exactly the bills /congress's pipeline
 * row for it counts. Not `?stage=`, which is a bill's current stage. */
export function reachedHref(stage: string): string {
  return `/congress/bills?reached=${encodeURIComponent(stage)}`;
}

/** The site's page for one roll call: the question, the result, each
 * party's split and every member's position. Every roll call has one,
 * whether or not it was on a bill (a nomination, a procedural motion). */
export function rollCallHref(v: {
  chamber: string;
  congress: number;
  session: number;
  number: number;
}): string {
  return `/congress/votes/${v.chamber}/${v.congress}/${v.session}/${v.number}`;
}

/** The Congress in session now, from the calendar (a new one convenes
 * January 3 of each odd year): the one a bill URL without ?congress= means. */
export function currentCongress(now: Date = new Date()): number {
  const y = now.getUTCFullYear();
  const beforeConvening = y % 2 === 1 && now.getUTCMonth() === 0 && now.getUTCDate() < 3;
  return Math.floor((y - 1789) / 2) + 1 - (beforeConvening ? 1 : 0);
}

/** The canonical path of a bill's page: ?congress= only for an earlier
 * Congress, so a current bill keeps the one URL it has always had. */
export function billCanonicalPath(
  billId: string,
  congress: number | null | undefined,
  now?: Date
): string {
  return billHref(billId, congress && congress !== currentCongress(now) ? congress : null);
}

const BILL_TYPES = new Set(["HR", "S", "HJRES", "SJRES", "HCONRES", "SCONRES", "HRES", "SRES"]);

/** The site's page for a bill, from any form of id the data carries
 * ("HR.8800", "H.R. 8800", the Clerk's "H R 8800"); null for anything that
 * isn't a bill, such as a roll call's own id or a nomination. Pass the
 * Congress whenever it is known (see billHref). */
export function billPageHref(
  billId: string | null | undefined,
  congress?: number | null
): string | null {
  const m = (billId ?? "")
    .replace(/[\s.]/g, "")
    .toUpperCase()
    .match(/^([A-Z]+)(\d+)$/);
  return m && BILL_TYPES.has(m[1]) ? billHref(`${m[1]}.${m[2]}`, congress) : null;
}

/** A ?congress= search parameter as a Congress number, or null. */
export function parseCongressParam(raw: string | string[] | undefined): number | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value || !/^\d{2,3}$/.test(value)) return null;
  const n = Number(value);
  return n >= 93 && n <= 200 ? n : null;
}

/** The chamber's own result, as the site colours it: carried, rejected, or
 * neither said (a quorum call). Never derived from the tally. */
export function resultTone(rejected: boolean | null): "yes" | "no" | "neutral" {
  if (rejected === false) return "yes";
  if (rejected === true) return "no";
  return "neutral";
}

export const CHAMBER_NAME = { senate: "Senate", house: "House" } as const;

/** A roll call's count as the backend words it (roll_call_shape: a broken
 * tie, a quorum call, an election of the Speaker); yeas to nays from a
 * backend image older than that field. */
export function voteTally(v: { tally?: string; yeas: number; nays: number }): string {
  return v.tally ?? `${v.yeas}–${v.nays}`;
}

/** What a roll call decided, as the backend words it ("Quorum call",
 * "Doe (TX) elected"); the chamber's result from an older backend. */
export function voteResult(v: { resultLabel?: string; result: string }): string {
  return v.resultLabel ?? v.result;
}

/** A roll call's count as a screen reader should say it: "50 yeas to 50
 * nays, tie broken by the Vice President of the United States voting Yea"
 * where the screen shows "50–50" (an en dash some readers skip, giving
 * "50 50"). A quorum call's or an election's count is in words already. */
export function spokenTally(v: {
  kind?: string;
  tally?: string;
  yeas: number;
  nays: number;
  tieBreaker?: { byWhom: string; vote: string } | null;
}): string {
  if (v.kind && v.kind !== "question") return voteTally(v);
  const tie = v.tieBreaker
    ? `, tie broken by the ${v.tieBreaker.byWhom} voting ${v.tieBreaker.vote}`
    : "";
  return `${v.yeas} yeas to ${v.nays} nays${tie}`;
}
