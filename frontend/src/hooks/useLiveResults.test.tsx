import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { RESULTS_POLL_MS, useLiveResults } from "./useLiveResults";

const fetchLiveResults = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchLiveResults }));

const phase = (p: string) => ({
  cycleYear: 2026,
  phase: { phase: p, electionDate: "2026-11-03", resultsUntil: null, lastResultChange: null },
  liveStates: [],
  senateStates: [],
  races: [],
  updates: [],
});

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  setVisibility("visible");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("useLiveResults", () => {
  it("asks once and stops during a campaign", async () => {
    fetchLiveResults.mockResolvedValue(phase("campaign"));
    renderHook(() => useLiveResults());
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS * 3));
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
  });

  it("polls while there are results, and not from a background tab", async () => {
    fetchLiveResults.mockResolvedValue(phase("results"));
    renderHook(() => useLiveResults("GA"));
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    expect(fetchLiveResults).toHaveBeenLastCalledWith("GA");

    act(() => setVisibility("hidden"));
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS * 5));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);

    // Shown again: catch up at once.
    await act(async () => setVisibility("visible"));
    expect(fetchLiveResults).toHaveBeenCalledTimes(3);
  });

  it("asks nothing when disabled", async () => {
    renderHook(() => useLiveResults("GA", false));
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(fetchLiveResults).not.toHaveBeenCalled();
  });

  it("keeps the last good count when a refresh fails", async () => {
    fetchLiveResults.mockResolvedValueOnce(phase("results")).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useLiveResults());
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(result.current.data?.phase.phase).toBe("results");
    expect(result.current.error).toBe("offline");
  });
});
