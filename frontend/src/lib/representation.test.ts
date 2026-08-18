import { describe, expect, it } from "vitest";
import { asciiScoreBar, getScoreLabel, getScoreColor, getScoreBgColor } from "./representation";

describe("getScoreLabel", () => {
  it("states a degree of representation and never asserts a cause", () => {
    // The score measures an outcome across three dimensions. None of them can
    // distinguish a captured member from one who is ineffective, thinly
    // documented, or out of step with their state — so no label may name a
    // mechanism. "DEEPLY CAPTURED" did, and is gone.
    const labels = [0, 10, 20, 21, 40, 41, 60, 61, 80, 81, 100].map(getScoreLabel);
    for (const label of labels) {
      expect(label).not.toMatch(/captur|bought|corrupt|bribe|owned|beholden/i);
    }
  });

  it("completes the ladder in one vocabulary", () => {
    expect(getScoreLabel(0)).toBe("NOT REPRESENTATIVE");
    expect(getScoreLabel(20)).toBe("NOT REPRESENTATIVE");
    expect(getScoreLabel(21)).toBe("WEAKLY REPRESENTATIVE");
    expect(getScoreLabel(41)).toBe("MIXED REPRESENTATION");
    expect(getScoreLabel(61)).toBe("REPRESENTATIVE");
    expect(getScoreLabel(81)).toBe("STRONGLY REPRESENTATIVE");
  });

  it("keeps the top band reachable — a scale that cannot say 'good' is a hit piece", () => {
    expect(getScoreLabel(100)).toBe("STRONGLY REPRESENTATIVE");
    expect(getScoreColor(100)).toBe("text-matrix-green");
  });

  it("did not move any band boundary while renaming the bottom one", () => {
    expect(getScoreLabel(20)).not.toBe(getScoreLabel(21));
    expect(getScoreLabel(40)).not.toBe(getScoreLabel(41));
    expect(getScoreLabel(60)).not.toBe(getScoreLabel(61));
    expect(getScoreLabel(80)).not.toBe(getScoreLabel(81));
  });
});

describe("score colours", () => {
  it("keeps text and bar tiers on identical cutoffs, so one score never renders two ways", () => {
    for (const n of [0, 20, 21, 40, 41, 60, 61, 80, 81, 100]) {
      expect(getScoreColor(n).replace("text-", "")).toBe(getScoreBgColor(n).replace("bg-", ""));
    }
  });
});

describe("asciiScoreBar", () => {
  it("always renders 20 cells so bars stay aligned in a column", () => {
    for (const n of [0, 1, 33, 50, 72, 99, 100]) {
      expect(asciiScoreBar(n)).toHaveLength(20);
    }
  });

  it("fills proportionally at the ends", () => {
    expect(asciiScoreBar(0)).toBe("░".repeat(20));
    expect(asciiScoreBar(100)).toBe("█".repeat(20));
  });
});
