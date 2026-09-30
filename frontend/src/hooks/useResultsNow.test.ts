import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useResultsNow } from "./useResultsNow";
import type { LiveResults } from "@/types/election";

// The browser's clock, set by each test.
const browser = vi.hoisted(() => ({ now: 0 }));
vi.mock("@/hooks/useNow", () => ({ useNow: () => browser.now }));

type Props = { results: Pick<LiveResults, "clock"> | null; failedAt: number | null };
const SERVER = Date.parse("2026-11-04T03:00:00Z");
const answer = (serverDate: number, receivedAt: number) => ({ clock: { serverDate, receivedAt } });

describe("the results pages' clock", () => {
  it("never runs backwards when a new answer's Date is behind where the last one had run on to", () => {
    browser.now = 1_000_000;
    const first = answer(SERVER, 1_000_000);
    const { result, rerender } = renderHook((p: Props) => useResultsNow(p.results, p.failedAt), {
      initialProps: { results: first, failedAt: null } as Props,
    });
    expect(result.current).toBe(SERVER);
    browser.now = 1_000_000 + 50_000;
    rerender({ results: first, failedAt: null });
    expect(result.current).toBe(SERVER + 50_000);
    // A copy with the original Date, received now (the browser's HTTP
    // cache answering a refetch): the clock holds where it stood.
    const copy = answer(SERVER, 1_000_000 + 50_000);
    rerender({ results: copy, failedAt: null });
    expect(result.current).toBe(SERVER + 50_000);
    // …and moves again once the new answer's own run-on passes that.
    browser.now = 1_000_000 + 70_000;
    rerender({ results: copy, failedAt: null });
    expect(result.current).toBe(SERVER + 50_000);
    browser.now = 1_000_000 + 110_000;
    rerender({ results: copy, failedAt: null });
    expect(result.current).toBe(SERVER + 60_000);
  });

  it("stops where it stood when a refresh fails, and never jumps back", () => {
    browser.now = 1_000_000;
    const first = answer(SERVER, 1_000_000);
    const { result, rerender } = renderHook((p: Props) => useResultsNow(p.results, p.failedAt), {
      initialProps: { results: first, failedAt: null } as Props,
    });
    browser.now = 1_000_000 + 61_000;
    rerender({ results: first, failedAt: null });
    expect(result.current).toBe(SERVER + 60_000);
    rerender({ results: first, failedAt: browser.now });
    expect(result.current).toBe(SERVER + 60_000);
    browser.now = 1_000_000 + 10 * 60_000;
    rerender({ results: first, failedAt: browser.now });
    expect(result.current).toBe(SERVER + 60_000);
  });

  it("lets no browser time set the floor before the first answer", () => {
    // A browser three hours fast: its time until an answer arrives, then
    // the server's, not held up at the browser's.
    browser.now = SERVER + 3 * 3_600_000;
    const { result, rerender } = renderHook((p: Props) => useResultsNow(p.results, p.failedAt), {
      initialProps: { results: null, failedAt: null } as Props,
    });
    expect(result.current).toBe(browser.now);
    rerender({ results: answer(SERVER, browser.now), failedAt: null });
    expect(result.current).toBe(SERVER);
  });
});
