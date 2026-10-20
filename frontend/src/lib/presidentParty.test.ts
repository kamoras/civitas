import { describe, expect, it } from "vitest";
import { presidentParty } from "./presidentParty";

describe("presidentParty", () => {
  it("names every party code GET /api/presidents sends", () => {
    // "D-R" is the API's code; it read as "No party" for four presidents.
    expect(presidentParty("D-R").label).toBe("Democratic-Republican");
    expect(presidentParty("W").label).toBe("Whig");
    expect(presidentParty("U").label).toBe("No party");
  });
});
