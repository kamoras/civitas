import { describe, expect, it } from "vitest";
import { developingSource, factsHeading } from "./developing";

describe("developing issue wording", () => {
  it("names what each kind was drafted from, never calling a rule or a count a vote", () => {
    expect(developingSource("federal_register_significant_rule")).toBe("a Federal Register rule");
    expect(developingSource("election_results")).toMatch(/election-night count/);
    expect(developingSource("house_roll_call_vote")).toBe("a House roll-call vote record");
    expect(developingSource(null)).toBe("a primary source");
  });

  it("heads a count's facts as the count, not as media coverage", () => {
    expect(factsHeading("election_results")).toBe("From the count");
    expect(factsHeading(null)).toBe("Media coverage");
  });
});
