import { describe, expect, it } from "vitest";
import { asOfPhrase, formatBracket } from "./disclosures";

describe("formatBracket", () => {
  it("renders a closed bracket as a range", () => {
    expect(formatBracket(15001, 50000, false)).toBe("$15,001 – $50,000");
  });

  it("renders the open-ended top bracket as a floor, never a range", () => {
    expect(formatBracket(50000000, 50000000, true)).toBe("$50,000,000+");
  });

  it("applies the same rule to compact sums", () => {
    const compact = (n: number) => `$${(n / 1e6).toFixed(1)}M`;
    expect(formatBracket(1e6, 5e6, false, compact)).toBe("$1.0M – $5.0M");
    expect(formatBracket(5e7, 5e7, true, compact)).toBe("$50.0M+");
  });
});

describe("asOfPhrase", () => {
  it("says year end for an annual report and the date for a new-filer snapshot", () => {
    expect(asOfPhrase("2025-12-31")).toBe("at year end");
    expect(asOfPhrase(null)).toBe("at year end");
    expect(asOfPhrase("2026-03-24")).toBe("on 2026-03-24");
  });
});
