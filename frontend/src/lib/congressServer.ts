import type { BillRecord, DayReport, MonthReport, PeriodReport } from "@/types/congress";
import { usableRecord } from "@/lib/ssrPayload";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// A session day's floor logs change every half hour; five minutes keeps a
// live day current without a request per reader.
const REVALIDATE_S = 300;

// The backend's two answers that mean "no such record": nothing recorded
// yet (404) and a date or month Congress can't have met (422).
const NO_RECORD = new Set([404, 422]);

/**
 * A record, or null when the backend says there is none. Anything else
 * (unreachable backend, 5xx, a body without the record's shape) throws, and
 * congress/error.tsx says the record could not be reached. Returning null for
 * an outage too made /congress read "no day of Congress has been recorded"
 * and made every dated report a 404, and a prerender or ISR pass cached that.
 * A throw is never cached: ISR keeps serving the last good page.
 */
async function getJson<T>(path: string, ...required: (keyof T & string)[]): Promise<T | null> {
  return getJsonWith<T>(path, { next: { revalidate: REVALIDATE_S } }, ...required);
}

async function getJsonWith<T>(path: string, init: RequestInit, ...required: (keyof T & string)[]): Promise<T | null> {
  const res = await fetch(`${BACKEND}${path}`, init);
  if (NO_RECORD.has(res.status)) return null;
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  const record = usableRecord<T>(await res.json(), ...required);
  if (!record) throw new Error(`${path}: response without ${required.join(", ")}`);
  return record;
}

export function fetchLatestDay(): Promise<DayReport | null> {
  return getJson<DayReport>("/api/congress/latest", "date", "chambers");
}

export function fetchDay(date: string): Promise<DayReport | null> {
  return getJson<DayReport>(`/api/congress/day/${date}`, "date", "chambers");
}

export function fetchWeek(date: string): Promise<PeriodReport | null> {
  return getJson<PeriodReport>(`/api/congress/week/${date}`, "start", "totals");
}

export function fetchMonth(month: string): Promise<MonthReport | null> {
  return getJson<MonthReport>(`/api/congress/month/${month}`, "start", "totals");
}

/** Null on any failure, not just a 404: the bill page still renders from the
 * site's own record of a tracked bill when Congress.gov's side is out. */
export function fetchBillRecord(billId: string, congress?: number | null): Promise<BillRecord | null> {
  const query = congress ? `?congress=${congress}` : "";
  // Not kept in Next's data cache: a record served partial (a part named in
  // `unavailable` because Congress.gov was slow or down) would be shown to
  // every reader for the cache's lifetime. The backend caches each part
  // that did arrive, so asking it again is cheap.
  return getJsonWith<BillRecord>(`/api/bills/${encodeURIComponent(billId)}/record${query}`, { cache: "no-store" }, "billId", "actions").catch(() => null);
}

export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
export const ISO_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
