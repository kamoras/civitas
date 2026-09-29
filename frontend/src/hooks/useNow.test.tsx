import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";
import { useNow, __resetNowTicker } from "./useNow";

function Clock({ label = "now", enabled }: { label?: string; enabled?: boolean }) {
  return <span data-testid={label}>{useNow(enabled)}</span>;
}

describe("useNow", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    __resetNowTicker();
  });

  afterEach(() => {
    cleanup();
    __resetNowTicker();
    vi.useRealTimers();
  });

  it("reports the current time and advances once a second", () => {
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    render(<Clock />);
    const start = Number(screen.getByTestId("now").textContent);
    expect(start).toBe(Date.now());

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(Number(screen.getByTestId("now").textContent)).toBe(start + 1000);
  });

  it("gives every subscriber the same instant", () => {
    render(
      <>
        <Clock label="a" />
        <Clock label="b" />
      </>
    );
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId("a").textContent).toBe(screen.getByTestId("b").textContent);
  });

  it("runs one timer no matter how many components read the clock", () => {
    const spy = vi.spyOn(globalThis, "setInterval");
    render(
      <>
        <Clock label="a" />
        <Clock label="b" />
        <Clock label="c" />
      </>
    );
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });

  it("stops ticking once the last reader unmounts", () => {
    const { unmount } = render(<Clock />);
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not re-render or start a timer when disabled", () => {
    const rendered = vi.fn();
    function Counted() {
      rendered();
      return <span>{useNow(false)}</span>;
    }
    render(<Counted />);
    expect(vi.getTimerCount()).toBe(0);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(rendered).toHaveBeenCalledTimes(1);
  });

  it("starts ticking when a disabled reader is enabled", () => {
    vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
    const { rerender } = render(<Clock enabled={false} />);
    vi.setSystemTime(new Date("2026-11-04T01:00:00Z"));
    rerender(<Clock enabled />);
    expect(Number(screen.getByTestId("now").textContent)).toBe(Date.parse("2026-11-04T01:00:00Z"));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(Number(screen.getByTestId("now").textContent)).toBe(Date.parse("2026-11-04T01:00:01Z"));
  });
});
