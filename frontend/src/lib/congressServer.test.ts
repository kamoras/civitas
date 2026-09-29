import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchBillRecord, fetchDay, fetchLatestDay } from "./congressServer";

const requestHeaders = vi.hoisted(() => ({ value: new Headers() }));
vi.mock("next/headers", () => ({ headers: async () => requestHeaders.value }));

afterEach(() => vi.unstubAllGlobals());

const answer = (status: number, body: unknown = {}) =>
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ status, ok: status < 300, json: async () => body })
  );

describe("congressServer: an outage never reads as no record", () => {
  it("returns the report", async () => {
    answer(200, { date: "2026-09-28", chambers: {} });
    expect(await fetchLatestDay()).toMatchObject({ date: "2026-09-28" });
  });

  it("returns null only when the backend says there is no record", async () => {
    answer(404);
    expect(await fetchLatestDay()).toBeNull();
    answer(422);
    expect(await fetchDay("1700-01-01")).toBeNull();
  });

  it("throws when the backend errors, is unreachable, or answers without the record", async () => {
    answer(500);
    await expect(fetchLatestDay()).rejects.toThrow("HTTP 500");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(fetchDay("2026-09-25")).rejects.toThrow("fetch failed");
    answer(200, {});
    await expect(fetchLatestDay()).rejects.toThrow("without date, chambers");
  });

  it("lets the bill page fall back to the tracked record when Congress.gov's side is out", async () => {
    answer(429);
    expect(await fetchBillRecord("s.1")).toEqual({ record: null, failed: true });
  });

  it("tells a bill that doesn't exist from one that couldn't be read", async () => {
    answer(404);
    expect(await fetchBillRecord("s.99999")).toEqual({ record: null, failed: false });
  });

  it("asks on the reader's behalf, so the lookup limit is per reader", async () => {
    // Without it every reader shared the frontend container's one bucket.
    requestHeaders.value = new Headers({ "x-real-ip": "203.0.113.9" });
    answer(200, { billId: "S.1", actions: [] });
    await fetchBillRecord("s.1");
    const init = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock
      .calls[0][1];
    expect(init.headers).toEqual({ "X-Forwarded-For": "203.0.113.9" });
    requestHeaders.value = new Headers();
  });
});
