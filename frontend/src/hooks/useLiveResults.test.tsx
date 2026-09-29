import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import {
  CAMPAIGN_POLL_MS,
  RESULTS_POLL_MS,
  describeInterval,
  electionIsNear,
  msUntilNear,
  useLiveResults,
} from "./useLiveResults";

const fetchLiveResults = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ fetchLiveResults }));

const phase = (p: string, electionDate = "2026-11-03") => ({
  cycleYear: 2026,
  phase: { phase: p, electionDate, resultsUntil: null, lastResultChange: null },
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
  // A month before election day unless a test says otherwise.
  vi.setSystemTime(new Date("2026-10-03T12:00:00Z"));
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
    fetchLiveResults
      .mockResolvedValueOnce(phase("results"))
      .mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useLiveResults());
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(result.current.data?.phase.phase).toBe("results");
    expect(result.current.error).toBe("offline");
  });

  it("keeps asking on the eve of election day, and switches when results start", async () => {
    vi.setSystemTime(new Date("2026-11-02T20:00:00Z"));
    fetchLiveResults
      .mockResolvedValueOnce(phase("campaign"))
      .mockResolvedValueOnce(phase("election_day"));
    const { result } = renderHook(() => useLiveResults("GA"));
    await act(async () => {});
    expect(result.current.data?.phase.phase).toBe("campaign");
    // Not every minute: a campaign page asks every ten.
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTime(CAMPAIGN_POLL_MS - RESULTS_POLL_MS));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    expect(result.current.data?.phase.phase).toBe("election_day");
    // Now polling at the results rate.
    fetchLiveResults.mockResolvedValue(phase("election_day"));
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(fetchLiveResults).toHaveBeenCalledTimes(3);
  });

  it("stops once the results window closes while the page is open", async () => {
    fetchLiveResults
      .mockResolvedValueOnce(phase("results"))
      .mockResolvedValue(phase("campaign", "2028-11-07"));
    const { result } = renderHook(() => useLiveResults());
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(RESULTS_POLL_MS));
    expect(result.current.data?.phase.phase).toBe("campaign");
    await act(async () => vi.advanceTimersByTime(CAMPAIGN_POLL_MS * 3));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
  });

  it("wakes a campaign page when election day comes near, and polls from then", async () => {
    // Open 12 days out: one answer, then nothing until 36h before the day.
    vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
    fetchLiveResults.mockResolvedValue(phase("campaign"));
    renderHook(() => useLiveResults());
    await act(async () => {});
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
    const near = Date.parse("2026-11-01T12:00:00Z");
    await act(async () => vi.advanceTimersByTime(near - Date.now() - 1));
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTime(1));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTime(CAMPAIGN_POLL_MS));
    expect(fetchLiveResults).toHaveBeenCalledTimes(3);
  });

  it("re-arms at setTimeout's ceiling for an election more than 24 days out", async () => {
    vi.setSystemTime(new Date("2026-06-01T12:00:00Z"));
    fetchLiveResults.mockResolvedValue(phase("campaign"));
    renderHook(() => useLiveResults());
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(2 ** 31 - 1));
    // One ask at the ceiling (still far), none at once on arming.
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTime(1000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
  });

  it("asks nothing more on a tab switch once a campaign answer is in", async () => {
    fetchLiveResults.mockResolvedValue(phase("campaign"));
    renderHook(() => useLiveResults());
    await act(async () => {});
    act(() => setVisibility("hidden"));
    await act(async () => setVisibility("visible"));
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
  });

  it("holds the backoff across a tab switch", async () => {
    fetchLiveResults.mockRejectedValue(new Error("502"));
    renderHook(() => useLiveResults());
    await act(async () => {});
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    // Waiting two minutes now. A quick tab switch mid-wait doesn't ask early…
    act(() => setVisibility("hidden"));
    await act(async () => vi.advanceTimersByTime(30_000));
    await act(async () => setVisibility("visible"));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTime(89_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    // …it asks when the wait is up.
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(3);
  });

  it("backs off while the endpoint keeps failing, and says how long it waits", async () => {
    fetchLiveResults.mockRejectedValue(new Error("502"));
    const { result } = renderHook(() => useLiveResults());
    await act(async () => {});
    expect(fetchLiveResults).toHaveBeenCalledTimes(1);
    expect(result.current.retryMs).toBe(60_000);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    expect(result.current.retryMs).toBe(120_000);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(fetchLiveResults).toHaveBeenCalledTimes(3);
    expect(result.current.retryMs).toBe(300_000);
    await act(async () => vi.advanceTimersByTime(300_000));
    expect(result.current.retryMs).toBe(600_000);
    // Recovery resets it.
    fetchLiveResults.mockResolvedValue(phase("results"));
    await act(async () => vi.advanceTimersByTime(600_000));
    expect(result.current.retryMs).toBeNull();
    expect(result.current.error).toBeNull();
  });
});

describe("electionIsNear", () => {
  const p = { electionDate: "2026-11-03" };
  it("is true from the eve of election day until two days after", () => {
    expect(electionIsNear(p, Date.parse("2026-11-01T11:00:00Z"))).toBe(false);
    expect(electionIsNear(p, Date.parse("2026-11-01T13:00:00Z"))).toBe(true);
    expect(electionIsNear(p, Date.parse("2026-11-04T23:00:00Z"))).toBe(true);
    expect(electionIsNear(p, Date.parse("2026-11-05T01:00:00Z"))).toBe(false);
    expect(electionIsNear(null)).toBe(false);
    expect(electionIsNear({ electionDate: "" })).toBe(false);
  });
});

describe("describeInterval", () => {
  it("says how long until election day comes near, and nothing once it has", () => {
    const p = { electionDate: "2026-11-03" };
    expect(msUntilNear(p, Date.parse("2026-11-01T11:00:00Z"))).toBe(3_600_000);
    expect(msUntilNear(p, Date.parse("2026-11-01T12:00:00Z"))).toBeNull();
    expect(msUntilNear(p, Date.parse("2026-11-10T12:00:00Z"))).toBeNull();
    expect(msUntilNear(p, Date.parse("2025-01-01T00:00:00Z"))).toBe(2 ** 31 - 1);
    expect(msUntilNear(null)).toBeNull();
  });

  it("names the wait in minutes", () => {
    expect(describeInterval(60_000)).toBe("every minute");
    expect(describeInterval(300_000)).toBe("every 5 minutes");
  });
});
