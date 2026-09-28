import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import SignalOverlapReading from "./SignalOverlapReading";
import type { SignalOverlap } from "@/types/scoreBreakdown";

const fetchSignalOverlap = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchSignalOverlap: (...args: unknown[]) => fetchSignalOverlap(...args),
}));

const labels: [string, string] = ["Legislative leadership", "Bipartisan coalition attraction"];

function overlap(house: SignalOverlap["chambers"]["house"]): SignalOverlap {
  return {
    actionR: 0.6,
    watchR: 0.4,
    chambers: {
      senate: {
        pairs: { effectiveness: { r: 0.083, n: 98, band: "ok", labels } },
        computedAt: "2026-09-28T04:10:00",
      },
      house,
    },
  };
}

describe("SignalOverlapReading", () => {
  beforeEach(() => fetchSignalOverlap.mockReset());

  it("reads each measured chamber with its own run date and the alert threshold", async () => {
    fetchSignalOverlap.mockResolvedValue(
      overlap({ pairs: { effectiveness: { r: -0.149, n: 404, band: "ok", labels } }, computedAt: "2026-09-27T06:00:00" }),
    );
    render(<SignalOverlapReading pair="effectiveness" />);
    const text = (await screen.findByText(/^Latest:/)).textContent;
    expect(text).toContain("Senate r = +0.083 across 98 members (distinct, run of 2026-09-28)");
    expect(text).toContain("House r = −0.149 across 404 members (distinct, run of 2026-09-27)");
    expect(text).toContain("|r| ≥ 0.60");
  });

  it("leaves out a chamber not yet measured", async () => {
    fetchSignalOverlap.mockResolvedValue(overlap(null));
    render(<SignalOverlapReading pair="effectiveness" />);
    const text = (await screen.findByText(/^Latest:/)).textContent;
    expect(text).not.toContain("House");
  });

  it("says so when nothing has been measured", async () => {
    const empty = overlap(null);
    empty.chambers.senate = null;
    fetchSignalOverlap.mockResolvedValue(empty);
    render(<SignalOverlapReading pair="constituent" />);
    expect(await screen.findByText(/Not measured yet/)).toBeTruthy();
  });

  it("says so when the reading can't be loaded", async () => {
    fetchSignalOverlap.mockRejectedValueOnce(new Error("down"));
    render(<SignalOverlapReading pair="effectiveness" />);
    expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
  });
});

describe("SignalOverlapReading precision", () => {
  it("never shows a value that reads as the alert threshold when it is below it", async () => {
    fetchSignalOverlap.mockResolvedValue({
      actionR: 0.6, watchR: 0.4,
      chambers: {
        senate: { pairs: { effectiveness: { r: 0.597, n: 98, band: "watch", labels } }, computedAt: null },
        house: null,
      },
    });
    render(<SignalOverlapReading pair="effectiveness" />);
    const text = (await screen.findByText(/^Latest:/)).textContent;
    expect(text).toContain("r = +0.597");
  });
});
