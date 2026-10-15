import { describe, expect, it } from "vitest";
import type { TrendPartyAudit } from "@/types/trendPosts";
import { auditStatus, formatMeasure, formatPValue, formatShare } from "./TrendPostsDashboard";

const audit = (over: Partial<TrendPartyAudit>): TrendPartyAudit => ({
  pool: { D: 10, R: 10, none: 3 },
  posted: { D: 6, R: 6, none: 1 },
  poolDemocraticShare: 0.5,
  postedDemocraticShare: 0.5,
  pValue: 1,
  minPosted: 10,
  alpha: 0.05,
  diverges: false,
  ...over,
});

describe("auditStatus", () => {
  it("says how many leaning posts the test still needs", () => {
    const s = auditStatus(audit({ posted: { D: 3, R: 2, none: 9 } }));
    expect(s.label).toBe("Too few posts (need 10 leaning, have 5)");
  });

  it("reports a divergence and a balance as such", () => {
    expect(auditStatus(audit({ diverges: true, pValue: 0.01 })).label).toBe("Diverges (p < 0.05)");
    expect(auditStatus(audit({})).label).toBe("Within expectation");
  });

  it("does not call an untestable audit balanced", () => {
    expect(auditStatus(audit({ pValue: null })).label).toBe("Not measured");
  });
});

describe("formatting", () => {
  it("shows a dash, never zero, for what was not measured", () => {
    expect(formatMeasure(null)).toBe("—");
    expect(formatShare(null)).toBe("—");
    expect(formatPValue(null)).toBe("—");
    expect(formatMeasure(0)).toBe("0.00");
  });

  it("formats shares and p-values", () => {
    expect(formatShare(0.456)).toBe("46% D");
    expect(formatPValue(0.0004)).toBe("<0.001");
    expect(formatPValue(0.2346)).toBe("0.235");
  });
});
