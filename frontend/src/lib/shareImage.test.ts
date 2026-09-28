import { describe, expect, it } from "vitest";
import { displayUrl, proxiedImageUrl, sectionUrl, shareFileName } from "./shareImage";

describe("proxiedImageUrl", () => {
  it("routes a bioguide photo through the same-origin photo route", () => {
    expect(proxiedImageUrl("https://bioguide.congress.gov/bioguide/photo/B/B001309.jpg")).toBe(
      "/photo/bioguide/B001309"
    );
  });

  // The route takes only an id, so anything else is read directly (and a
  // host without CORS headers is simply left out of the picture) — never
  // handed to a server-side fetch.
  it("leaves every other URL alone", () => {
    expect(proxiedImageUrl("https://example.com/photo/B/B001309.jpg")).toBeNull();
    expect(proxiedImageUrl("https://bioguide.congress.gov/bioguide/photo/B/../x.jpg")).toBeNull();
    expect(proxiedImageUrl("/_next/static/media/a.png")).toBeNull();
  });
});

describe("sectionUrl / displayUrl", () => {
  it("appends the section anchor, replacing any existing one", () => {
    expect(sectionUrl("https://civitas-research.org/politicians/x", "votes")).toBe(
      "https://civitas-research.org/politicians/x#votes"
    );
    expect(sectionUrl("https://civitas-research.org/politicians/x#old", "votes")).toBe(
      "https://civitas-research.org/politicians/x#votes"
    );
  });

  it("prints the link without its scheme", () => {
    expect(displayUrl("https://civitas-research.org/issue/i1234abcd")).toBe(
      "civitas-research.org/issue/i1234abcd"
    );
  });
});

describe("shareFileName", () => {
  it("names the file by page and section", () => {
    expect(
      shareFileName("https://civitas-research.org/politicians/tim-burchett", "funding-independence")
    ).toBe("civitas-tim-burchett-funding-independence.png");
  });

  it("ignores the query string and cleans up odd characters", () => {
    expect(shareFileName("https://civitas-research.org/action?issue=i1", "issue-i1")).toBe(
      "civitas-action-issue-i1.png"
    );
    expect(shareFileName("https://civitas-research.org/congress/bills/H.R.%201", "Votes")).toBe(
      "civitas-h-r-201-votes.png"
    );
  });
});
