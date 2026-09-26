import { describe, expect, it } from "vitest";
import {
  formatCompact,
  formatMs,
  formatSecondsShort,
  niceDurationTicks,
  niceTicks,
  xLabelIndices,
} from "./scale";

describe("niceTicks", () => {
  it("starts at zero and rounds the ceiling up to a clean step", () => {
    expect(niceTicks(237)).toEqual([0, 100, 200, 300]);
    expect(niceTicks(900)).toEqual([0, 250, 500, 750, 1000]);
  });

  it("keeps an exact nice maximum rather than adding a step past it", () => {
    expect(niceTicks(1000)).toEqual([0, 250, 500, 750, 1000]);
  });

  it("handles fractional and tiny ranges without float noise", () => {
    expect(niceTicks(0.7)).toEqual([0, 0.2, 0.4, 0.6, 0.8]);
    expect(niceTicks(3)).toEqual([0, 1, 2, 3]);
  });

  it("gives a usable axis for an all-zero series", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
  });
});

describe("niceDurationTicks", () => {
  it("steps in clock units, not decimal ones", () => {
    // 1h 49m: 30-minute steps, not 2,000-second ones.
    expect(niceDurationTicks(6540)).toEqual([0, 1800, 3600, 5400, 7200]);
    expect(niceDurationTicks(6540).map(formatSecondsShort)).toEqual([
      "0s",
      "30m",
      "1h",
      "1.5h",
      "2h",
    ]);
    expect(niceDurationTicks(83)).toEqual([0, 30, 60, 90]);
  });
});

describe("xLabelIndices", () => {
  it("labels every point when they fit", () => {
    expect(xLabelIndices(4, 6)).toEqual([0, 1, 2, 3]);
  });

  it("thins to an even stride that always ends on the last point", () => {
    const picked = xLabelIndices(30, 5);
    expect(picked[picked.length - 1]).toBe(29);
    expect(picked.length).toBeLessThanOrEqual(5);
    const gaps = picked.slice(1).map((v, i) => v - picked[i]);
    expect(new Set(gaps).size).toBe(1);
  });

  it("is empty for no points", () => {
    expect(xLabelIndices(0, 5)).toEqual([]);
  });
});

describe("formatters", () => {
  it("compacts large counts", () => {
    expect(formatCompact(950)).toBe("950");
    expect(formatCompact(1284)).toBe("1.3K");
    expect(formatCompact(25_000)).toBe("25K");
    expect(formatCompact(4_200_000)).toBe("4.2M");
  });

  it("formats milliseconds and seconds for axes", () => {
    expect(formatMs(850)).toBe("850ms");
    expect(formatMs(1500)).toBe("1.5s");
    expect(formatSecondsShort(45)).toBe("45s");
    expect(formatSecondsShort(5400)).toBe("1.5h");
  });
});
