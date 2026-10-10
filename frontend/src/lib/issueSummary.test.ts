import { describe, expect, it } from "vitest";
import { summaryRestatesTitle } from "./issueSummary";

describe("summaryRestatesTitle", () => {
  it("catches a lede that is the headline with a period", () => {
    expect(summaryRestatesTitle("Senate passes the bill", "Senate passes the bill.")).toBe(true);
  });

  it("ignores case and punctuation", () => {
    expect(summaryRestatesTitle("Agency’s rule takes effect", "agency's rule takes effect")).toBe(
      true
    );
  });

  it("keeps a summary that says more", () => {
    expect(
      summaryRestatesTitle("Senate passes the bill", "Senate passes the bill, 52 to 48.")
    ).toBe(false);
  });

  it("is false for no summary", () => {
    expect(summaryRestatesTitle("Senate passes the bill", "")).toBe(false);
    expect(summaryRestatesTitle("Senate passes the bill", null)).toBe(false);
  });
});
