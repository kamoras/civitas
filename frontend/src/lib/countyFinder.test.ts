import { describe, expect, it } from "vitest";
import { countyFinderStatus, countyNames } from "./countyFinder";

const lines = {
  state: "ZZ",
  congress: 119,
  available: true,
  districts: [
    { district: 1, counties: ["Alpha County (part)", "Beta County"] },
    { district: 2, counties: ["Alpha County (part)", "Alder County"] },
  ],
};

describe("countyNames", () => {
  it("lists each county once, without the split tag", () => {
    expect(countyNames(lines)).toEqual(["Alder County", "Alpha County", "Beta County"]);
    expect(countyNames(null)).toEqual([]);
  });
});

describe("countyFinderStatus", () => {
  const names = countyNames(lines);
  it("says nothing until something is typed", () => {
    expect(countyFinderStatus(names, "  ", 0)).toBeNull();
  });
  it("names a split county's districts as more than one member", () => {
    expect(countyFinderStatus(names, "alpha", 2)).toMatch(/Alpha County is split between 2/);
  });
  it("names a whole county's one district", () => {
    expect(countyFinderStatus(names, "beta", 1)).toBe("One district covers Beta County.");
  });
  it("asks to narrow a query that matches several counties", () => {
    expect(countyFinderStatus(names, "al", 2)).toMatch(/^2 counties match/);
  });
  it("says when nothing matches", () => {
    expect(countyFinderStatus(names, "gamma", 0)).toBe("No district matches that county.");
  });
});
