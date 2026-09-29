import type { BillRecord, DayReport, MonthReport, PeriodReport } from "@/types/congress";
import { headers } from "next/headers";
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

async function getJsonWith<T>(
  path: string,
  init: RequestInit,
  ...required: (keyof T & string)[]
): Promise<T | null> {
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

/** A bill's Congress.gov record, or null. `failed` says why it is null: false
 * for "no such bill" (404/422), true for anything else — the backend's
 * per-visitor lookup limit, the hourly budget, an outage. The bill page still
 * renders from the site's own record of a tracked bill when this fails, and
 * is an error page (not a 404) when it has neither. */
export async function fetchBillRecord(
  billId: string,
  congress?: number | null
): Promise<{ record: BillRecord | null; failed: boolean }> {
  const query = congress ? `?congress=${congress}` : "";
  try {
    const record = await getJsonWith<BillRecord>(
      `/api/bills/${encodeURIComponent(billId)}/record${query}`,
      // Not kept in Next's data cache: a record served partial (a part named
      // in `unavailable` because Congress.gov was slow or down) would be
      // shown to every reader for the cache's lifetime. The backend caches
      // each part that did arrive, so asking it again is cheap.
      { cache: "no-store", headers: await visitorHeaders() },
      "billId",
      "actions"
    );
    return { record, failed: false };
  } catch {
    return { record: null, failed: true };
  }
}

/** The reader's address for a backend route that limits lookups per
 * visitor (api/rate_limit.py). This request comes from the frontend's
 * container, not the reader's browser: without the header every reader
 * shared one ten-a-minute bucket, and past it bill pages lost their
 * Congress.gov record — and an untracked bill answered 404. nginx sets
 * X-Real-IP from its own view of the client, and the backend trusts
 * X-Forwarded-For only from a private-network peer. */
async function visitorHeaders(): Promise<Record<string, string>> {
  try {
    const ip = (await headers()).get("x-real-ip");
    return ip ? { "X-Forwarded-For": ip } : {};
  } catch {
    return {}; // outside a request (a build-time render)
  }
}

export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
export const ISO_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
