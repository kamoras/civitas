import { describe, expect, it } from "vitest";
import { formatBracket } from "./disclosures";

describe("formatBracket", () => {
  it("renders a closed bracket as a range", () => {
    expect(formatBracket(15001, 50000, false)).toBe("$15,001 – $50,000");
  });

  it("renders the open-ended top bracket as a floor, never a range", () => {
    expect(formatBracket(50000000, 50000000, true)).toBe("$50,000,000+");
  });
});
