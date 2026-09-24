import { describe, expect, it } from "vitest";

import { fundingShareBase, pacSharePct } from "./funding";

describe("funding shares", () => {
  it("uses contributions, not receipts, as the base", () => {
    // $50M receipts, $30M of it JFC transfers: PACs gave $5M of $20M contributed.
    expect(pacSharePct(5_000_000, { totalRaised: 50_000_000, totalContributions: 20_000_000 })).toBe(25);
  });

  it("falls back to total raised for records without contributions", () => {
    expect(fundingShareBase({ totalRaised: 10, totalContributions: null })).toBe(10);
    expect(pacSharePct(1, { totalRaised: 10 })).toBe(10);
  });

  it("is zero when nothing was raised", () => {
    expect(pacSharePct(5, { totalRaised: 0, totalContributions: 0 })).toBe(0);
  });
});
