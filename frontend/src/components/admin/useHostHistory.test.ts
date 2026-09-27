import { describe, expect, it } from "vitest";
import { cpuUtilisation, formatPct } from "./useHostHistory";

describe("cpuUtilisation", () => {
  it("is the busy share of the ticks between two readings", () => {
    expect(cpuUtilisation({ busy: 100, total: 1000 }, { busy: 130, total: 1400 })).toBeCloseTo(7.5);
  });

  it("needs two readings, and gives up on a reset or an empty interval", () => {
    expect(cpuUtilisation(null, { busy: 1, total: 2 })).toBeNull();
    expect(cpuUtilisation({ busy: 1, total: 2 }, null)).toBeNull();
    expect(cpuUtilisation({ busy: 500, total: 9000 }, { busy: 10, total: 100 })).toBeNull();
    expect(cpuUtilisation({ busy: 5, total: 50 }, { busy: 5, total: 50 })).toBeNull();
  });
});

describe("formatPct", () => {
  it("keeps a decimal for small values so an idle host doesn't read 0%", () => {
    expect(formatPct(0.4)).toBe("0.4%");
    expect(formatPct(37.2)).toBe("37%");
  });
});
