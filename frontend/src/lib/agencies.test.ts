import { describe, expect, it } from "vitest";
import { matchAgencies } from "./agencies";

const A = (name: string, shortName: string | null = null) => ({ name, shortName });
const LIST = [
  A("Agriculture Department", "USDA"),
  A("Defense Department", "DOD"),
  A("Environmental Protection Agency", "EPA"),
  A("Federal Bureau of Investigation", "FBI"),
  A("Forest Service"),
  A("Internal Revenue Service", "IRS"),
  A("Justice Department", "DOJ"),
  A("Treasury Department"),
];

describe("matchAgencies", () => {
  it("finds an agency by its acronym first, never by letters inside a word", () => {
    for (const [q, name] of [
      ["EPA", "Environmental Protection Agency"],
      ["irs", "Internal Revenue Service"],
      ["FBI", "Federal Bureau of Investigation"],
    ]) {
      const got = matchAgencies(LIST, q);
      expect(got[0].name).toBe(name);
      // "epa" is inside "Department": a substring match listed every department.
      expect(got).toHaveLength(1);
    }
  });

  it("matches the start of each typed word, an exact name ranking first", () => {
    expect(matchAgencies(LIST, "forest").map((a) => a.name)).toEqual(["Forest Service"]);
    expect(matchAgencies(LIST, "treasury department").map((a) => a.name)).toEqual([
      "Treasury Department",
    ]);
    expect(matchAgencies(LIST, "dep")).toHaveLength(4);
    expect(matchAgencies(LIST, "  ")).toEqual([]);
  });
});
