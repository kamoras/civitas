import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The module keeps a once-per-page-load flag, so each test gets a fresh copy.
async function freshBeacon() {
  vi.resetModules();
  return (await import("./LoadTimingBeacon")).default;
}

describe("LoadTimingBeacon", () => {
  const sendBeacon = vi.fn<(url: string) => boolean>(() => true);

  beforeEach(() => {
    vi.useFakeTimers();
    sendBeacon.mockClear();
    Object.defineProperty(navigator, "sendBeacon", { value: sendBeacon, configurable: true });
    Object.defineProperty(document, "readyState", { value: "complete", configurable: true });
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    vi.spyOn(performance, "getEntriesByType").mockReturnValue([
      { responseStart: 120.4, loadEventEnd: 910.6 } as unknown as PerformanceEntry,
    ]);
    vi.spyOn(performance, "getEntriesByName").mockReturnValue([
      { startTime: 400.2 } as unknown as PerformanceEntry,
    ]);
    window.history.replaceState(null, "", "/leaderboard");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("reports one load's rounded timings, once", async () => {
    const Beacon = await freshBeacon();
    const { unmount } = render(<Beacon />);
    vi.runAllTimers();
    unmount();
    render(<Beacon />);
    vi.runAllTimers();

    expect(sendBeacon).toHaveBeenCalledTimes(1);
    const url = new URL(sendBeacon.mock.calls[0][0], "http://x");
    expect(url.pathname).toBe("/api/track-timing");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      path: "/leaderboard",
      ttfb: "120",
      fcp: "400",
      load: "911",
    });
  });

  it("does not report the admin dashboard", async () => {
    window.history.replaceState(null, "", "/admin");
    const Beacon = await freshBeacon();
    render(<Beacon />);
    vi.runAllTimers();
    expect(sendBeacon).not.toHaveBeenCalled();
  });

  it("does not report a page loaded in a background tab", async () => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    const Beacon = await freshBeacon();
    render(<Beacon />);
    vi.runAllTimers();
    expect(sendBeacon).not.toHaveBeenCalled();
  });
});
