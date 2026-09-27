import type { BillRecord, DayReport, MonthReport, PeriodReport } from "@/types/congress";
import { usableRecord } from "@/lib/ssrPayload";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// A session day's floor logs change every half hour; five minutes keeps a
// live day current without a request per reader.
const REVALIDATE_S = 300;

async function getJson<T>(path: string, ...required: (keyof T & string)[]): Promise<T | null> {
  try {
    const res = await fetch(`${BACKEND}${path}`, { next: { revalidate: REVALIDATE_S } });
    if (!res.ok) return null;
    return usableRecord<T>(await res.json(), ...required);
  } catch {
    return null;
  }
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

export function fetchBillRecord(billId: string): Promise<BillRecord | null> {
  return getJson<BillRecord>(`/api/bills/${encodeURIComponent(billId)}/record`, "billId", "actions");
}

export const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
export const ISO_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
