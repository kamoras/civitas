import { describe, expect, it } from "vitest";
import { countyFinderStatus, countyNames, finderSuggestions } from "./countyFinder";

const lines = {
  state: "ZZ",
  congress: 119,
  available: true,
  districts: [
    {
      district: 1,
      counties: ["Alpha County (part)", "Beta County"],
      places: ["Rivertown city", "Oakdale"],
    },
    { district: 2, counties: ["Alpha County (part)", "Alder County"], places: ["Rivertown city"] },
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
    expect(countyFinderStatus(names, "gamma", 0)).toBe(
      "No district matches that city, town or county."
    );
  });
  it("lets a place typed in full speak for itself", () => {
    expect(countyFinderStatus(names, "rivertown", 2, ["Rivertown is in districts 1 and 2."])).toBe(
      "Rivertown is in districts 1 and 2."
    );
  });
  it("asks for the full name when only part of a place matched", () => {
    expect(countyFinderStatus(names, "river", 2)).toBe(
      "2 districts match. Type the full name of your city, town or county to see which districts it is in."
    );
  });
});

describe("finderSuggestions", () => {
  it("offers each county, then each place by the name people use, once", () => {
    expect(finderSuggestions(lines)).toEqual([
      "Alder County",
      "Alpha County",
      "Beta County",
      "Oakdale",
      "Rivertown",
    ]);
  });
});
