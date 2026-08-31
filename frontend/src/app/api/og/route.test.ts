import { describe, expect, it } from "vitest";
import { formatStanding } from "./route";

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
