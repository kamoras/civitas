import { describe, expect, it } from "vitest";
import { countIsOfficial, developingSource, factsHeading, factsSectionId } from "./developing";

describe("developing issue wording", () => {
  it("names what each kind was drafted from, never calling a rule or a count a vote", () => {
    expect(developingSource("federal_register_significant_rule")).toBe("a Federal Register rule");
    expect(developingSource("election_results")).toMatch(/election-night count/);
    expect(developingSource("house_roll_call_vote")).toBe("a House roll-call vote record");
    expect(developingSource(null)).toBe("a primary source");
  });

  it("never calls an official count 'not final'", () => {
    expect(developingSource("election_results", { countOfficial: false })).toMatch(
      /which is not final$/
    );
    const official = developingSource("election_results", { countOfficial: true });
    expect(official).not.toMatch(/not final/);
    expect(official).toMatch(/which the state lists as official$/);
    // The flag is about counts only.
    expect(developingSource("house_roll_call_vote", { countOfficial: true })).toBe(
      "a House roll-call vote record"
    );
  });

  it("heads a count's facts as the count, not as media coverage", () => {
    expect(factsHeading({ sourceType: "election_results", status: "developing" })).toBe(
      "From the count"
    );
    expect(factsHeading({ sourceType: null, status: "confirmed" })).toBe("In the coverage");
  });

  it("heads a confirmed count issue's facts as media coverage: promotion swaps them for the outlets'", () => {
    const promoted = { sourceType: "election_results", status: "confirmed" };
    expect(factsHeading(promoted)).toBe("In the coverage");
    expect(factsSectionId(promoted)).toBe("media-coverage");
  });

  it("derives the facts section's id from its heading, keeping media-coverage for news", () => {
    expect(factsSectionId({ sourceType: "election_results", status: "developing" })).toBe(
      "from-the-count"
    );
    expect(factsSectionId({ sourceType: null, status: "confirmed" })).toBe("media-coverage");
    expect(factsSectionId({ sourceType: "senate_roll_call_vote", status: "developing" })).toBe(
      "media-coverage"
    );
  });

  it("takes the backend's own official flag when it sends one", () => {
    expect(countIsOfficial({ title: "Democrat leads …", countOfficial: true })).toBe(true);
    expect(
      countIsOfficial({ title: "Democrat wins … in the official count, …", countOfficial: false })
    ).toBe(false);
  });

  it("reads a count as official only from the backend's official-count title", () => {
    expect(
      countIsOfficial({
        title:
          "Democrat wins Utah's 1st Congressional District in the official count, taking a seat Republicans held",
      })
    ).toBe(true);
    expect(
      countIsOfficial({
        title: "Democrat leads Utah's 1st Congressional District count in a seat Republicans hold",
      })
    ).toBe(false);
  });
});
