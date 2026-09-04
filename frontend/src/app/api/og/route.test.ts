import { describe, expect, it } from "vitest";
import { formatStanding, parseIssueId, parseStateCode } from "./route";

describe("formatStanding", () => {
  it("formats a senator as party-state, no district", () => {
    expect(formatStanding({ party: "D", state: "CA" })).toBe("D-CA");
  });

  it("formats a representative as party-state-district", () => {
    expect(formatStanding({ party: "R", state: "TX", district: 12 })).toBe("R-TX-12");
  });

  // FEC codes at-large districts as "00" -> district 0. The bug this
  // guards against: a truthy `identity?.district ?` check treated 0 like
  // "no district", silently rendering an at-large representative in the
  // Senate's party-state format instead of party-state-AL. Same bug
  // class already guarded in lib/elections.test.ts.
  it("formats an at-large district (0) as AL, not as a missing district", () => {
    expect(formatStanding({ party: "D", state: "AK", district: 0 })).toBe("D-AK-AL");
  });

  it("returns an empty string when neither state nor district is known", () => {
    expect(formatStanding({})).toBe("");
    expect(formatStanding(undefined)).toBe("");
  });
});

describe("parseIssueId", () => {
  // The bug this guards against: every real link on the site points at
  // issue.publicId ("i" + 8 hex chars, e.g. "i9e3779b1"), never the raw
  // numeric id — a digits-only check here rejected every real request
  // and silently fell through to the generic fallback card for every
  // issue, photo or not, regardless of how much real content it had.
  it("accepts a real public id", () => {
    expect(parseIssueId("i9e3779b1")).toBe("i9e3779b1");
  });

  it("accepts a public id regardless of letter case", () => {
    expect(parseIssueId("I9E3779B1")).toBe("I9E3779B1");
  });

  it("still accepts a legacy bare numeric id", () => {
    expect(parseIssueId("650")).toBe("650");
  });

  it("rejects a public id with the wrong hex length", () => {
    expect(parseIssueId("i9e3779b")).toBeNull();
    expect(parseIssueId("i9e3779b12")).toBeNull();
  });

  it("rejects a non-hex suffix", () => {
    expect(parseIssueId("i9e3779zz")).toBeNull();
  });

  it("rejects garbage and empty input", () => {
    expect(parseIssueId("not-an-id")).toBeNull();
    expect(parseIssueId("")).toBeNull();
    expect(parseIssueId(null)).toBeNull();
  });
});

describe("parseStateCode", () => {
  it("accepts a real state code and uppercases it", () => {
    expect(parseStateCode("tx")).toBe("TX");
    expect(parseStateCode("CA")).toBe("CA");
  });

  it("accepts DC", () => {
    expect(parseStateCode("dc")).toBe("DC");
  });

  it("rejects anything that isn't exactly 2 letters", () => {
    expect(parseStateCode("texas")).toBeNull();
    expect(parseStateCode("t")).toBeNull();
    expect(parseStateCode("12")).toBeNull();
  });

  it("rejects garbage and empty input", () => {
    expect(parseStateCode("")).toBeNull();
    expect(parseStateCode(null)).toBeNull();
  });
});
