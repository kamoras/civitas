import { describe, expect, it } from "vitest";
import {
  billCanonicalPath,
  billHref,
  billPageHref,
  spokenTally,
  currentCongress,
  parseCongressParam,
} from "./congress";

describe("bill links name their Congress", () => {
  it("adds ?congress= whenever the Congress is known", () => {
    expect(billHref("S.1", 118)).toBe("/congress/bills/S.1?congress=118");
    expect(billHref("S.1")).toBe("/congress/bills/S.1");
    expect(billPageHref("H.R. 8800", 119)).toBe("/congress/bills/HR.8800?congress=119");
    expect(billPageHref("PN11-19", 119)).toBeNull();
  });

  it("keeps a current bill's canonical URL bare, an earlier one's with its Congress", () => {
    const now = new Date(Date.UTC(2026, 8, 28));
    expect(billCanonicalPath("S.1", 119, now)).toBe("/congress/bills/S.1");
    expect(billCanonicalPath("S.1", 118, now)).toBe("/congress/bills/S.1?congress=118");
  });

  it("changes Congress on January 3 of an odd year", () => {
    expect(currentCongress(new Date(Date.UTC(2027, 0, 2)))).toBe(119);
    expect(currentCongress(new Date(Date.UTC(2027, 0, 3)))).toBe(120);
    expect(currentCongress(new Date(Date.UTC(2026, 11, 31)))).toBe(119);
  });

  it("reads only a plausible ?congress=", () => {
    expect(parseCongressParam("118")).toBe(118);
    expect(parseCongressParam(["117", "118"])).toBe(117);
    for (const bad of [undefined, "", "abc", "5", "9999", "118.5"])
      expect(parseCongressParam(bad)).toBeNull();
  });
});

describe("spokenTally", () => {
  it("spells out a count and a broken tie for a screen reader", () => {
    expect(
      spokenTally({
        kind: "question",
        yeas: 50,
        nays: 50,
        tieBreaker: { byWhom: "Vice President of the United States", vote: "Yea" },
      })
    ).toBe("50 yeas to 50 nays, tie broken by the Vice President of the United States voting Yea");
    expect(spokenTally({ kind: "quorum", tally: "433 present", yeas: 0, nays: 0 })).toBe(
      "433 present"
    );
  });
});
