import { describe, expect, it } from "vitest";
import { appointerEstimate } from "./justices";
import type { JusticeLoyalty } from "@/types/justice";

const v3: JusticeLoyalty = {
  estimate: 0.1,
  se: 0.05,
  ciLow: 0.002,
  ciHigh: 0.198,
  votesIn: 50,
  votesOut: 200,
  rateIn: 0.6,
  rateOut: 0.5,
  throughTerm: 2025,
};

describe("appointerEstimate", () => {
  it("shows a justice v3 estimate", () => {
    expect(appointerEstimate(v3)).toBe(v3);
  });

  it("treats a pre-v3 response (shrunk estimate, no interval) as not measured", () => {
    const v2 = { ...v3, ciLow: undefined, ciHigh: undefined };
    expect(appointerEstimate(v2 as unknown as JusticeLoyalty)).toBeNull();
    expect(appointerEstimate(null)).toBeNull();
  });
});
