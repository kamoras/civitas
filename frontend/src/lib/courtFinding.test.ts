import { describe, expect, it } from "vitest";
import { courtFinding } from "@/lib/justices";
import type { AppointerFinding } from "@/types/court";

const finding = (over: Partial<AppointerFinding> = {}): AppointerFinding => ({
  estimate: 0.0246,
  low: -0.0066,
  high: 0.0562,
  firstTerm: 1937,
  lastTerm: 2025,
  justices: 43,
  draws: 200,
  excludesZero: false,
  scdbRelease: "2026_01",
  source: "x",
  ...over,
});

describe("courtFinding", () => {
  it("states the backend's figures, never typed ones", () => {
    expect(courtFinding(finding())).toBe(
      "Across every justice since 1937, justices side with the federal government about 2.5 points more often while their appointing president is in office, once career timing is set aside (95% interval −0.7 to +5.6): not distinguishable from no effect."
    );
  });

  it("follows the sign and the interval the backend decided", () => {
    const s = courtFinding(
      finding({ estimate: -0.03, low: -0.05, high: -0.01, excludesZero: true })
    );
    expect(s).toContain("3.0 points less often");
    expect(s).toContain(": distinguishable from no effect.");
  });

  it("says nothing without the figures", () => {
    expect(courtFinding(null)).toBeNull();
  });
});
