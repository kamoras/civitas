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

  it("completes the ladder in one vocabulary, on unmoved band boundaries", () => {
    // Both sides of every cutoff: renaming the bottom band must not have
    // moved any boundary.
    expect(getScoreLabel(0)).toBe("Not representative");
    expect(getScoreLabel(20)).toBe("Not representative");
    expect(getScoreLabel(21)).toBe("Weakly representative");
    expect(getScoreLabel(40)).toBe("Weakly representative");
    expect(getScoreLabel(41)).toBe("Mixed representation");
    expect(getScoreLabel(60)).toBe("Mixed representation");
    expect(getScoreLabel(61)).toBe("Representative");
    expect(getScoreLabel(80)).toBe("Representative");
    expect(getScoreLabel(81)).toBe("Strongly representative");
  });

  it("keeps the top band reachable — a scale that cannot say 'good' is a hit piece", () => {
    expect(getScoreLabel(100)).toBe("Strongly representative");
    expect(getScoreColor(100)).toBe("text-phos");
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
