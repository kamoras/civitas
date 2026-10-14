import { describe, expect, it } from "vitest";
import { newDistrictSentence, oldDistrictSentence, redistrictingHref } from "./redistricting";

const base = { predecessorNumber: 5, pviBefore: 3, pviAfter: 9, unchanged: false };

describe("newDistrictSentence", () => {
  it("says most when the predecessor holds a majority", () => {
    expect(newDistrictSentence({ ...base, predecessorSharePct: 78 })).toBe(
      "Most of this district's residents (78%) were in the old 5th; its lean moved from R+3 to R+9."
    );
  });
  it("says the largest group when no old district holds a majority", () => {
    expect(
      newDistrictSentence({ ...base, predecessorSharePct: 41, pviBefore: -23, pviAfter: 10 })
    ).toBe(
      "The largest group of this district's residents (41%) were in the old 5th, which leaned D+23; this district leans R+10."
    );
  });
  it("says the same lean when a majority predecessor's lean is unchanged", () => {
    expect(
      newDistrictSentence({ ...base, predecessorSharePct: 60, pviBefore: -2, pviAfter: -2 })
    ).toBe(
      "Most of this district's residents (60%) were in the old 5th; its lean is D+2, the same as before."
    );
  });
  it("names an unchanged district as unchanged", () => {
    expect(
      newDistrictSentence({ ...base, predecessorSharePct: 100, unchanged: true, pviBefore: 9 })
    ).toBe("Unchanged: the same residents as the old 5th. Its lean is R+9.");
  });
  it("drops the lean when asked (results mode)", () => {
    expect(newDistrictSentence({ ...base, predecessorSharePct: 78 }, false)).toBe(
      "Most of this district's residents (78%) were in the old 5th."
    );
  });
  it("words an at-large predecessor", () => {
    expect(
      newDistrictSentence({ ...base, predecessorNumber: 0, predecessorSharePct: 60 })
    ).toContain("the old at-large");
  });
});

describe("oldDistrictSentence", () => {
  const d = (number: number, sharePct: number) => ({
    district: `ZZ-${number}`,
    number,
    residents: 1,
    sharePct,
  });
  it("lists up to three destinations and counts the rest", () => {
    expect(
      oldDistrictSentence({
        number: 5,
        hasSuccessor: true,
        destinations: [d(5, 70), d(6, 15), d(7, 8), d(8, 4), d(9, 3)],
      })
    ).toBe(
      "Of the old 5th's residents, 70% are in the new 5th, 15% in the new 6th and 8% in the new 7th, and the rest in 2 other districts."
    );
  });
  it("says all when nobody moved, and flags a district with no successor", () => {
    expect(oldDistrictSentence({ number: 2, hasSuccessor: true, destinations: [d(2, 100)] })).toBe(
      "All of the old 2nd's residents are in the new 2nd."
    );
    expect(
      oldDistrictSentence({ number: 3, hasSuccessor: false, destinations: [d(1, 50), d(2, 50)] })
    ).toContain("No new district draws most of its residents from it.");
  });
});

it("links a state page and a district anchor", () => {
  expect(redistrictingHref("tx", "new-5")).toBe("/elections/redistricting/TX#new-5");
});
