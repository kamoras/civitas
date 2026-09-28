import { describe, expect, it } from "vitest";
import { parseBioguideId } from "./route";

describe("parseBioguideId", () => {
  it("accepts a bioguide id", () => {
    expect(parseBioguideId("B001309")).toBe("B001309");
  });

  // The route fetches from bioguide by this id; anything that isn't one
  // must never reach the outgoing URL.
  it("rejects anything else", () => {
    expect(parseBioguideId("b001309")).toBeNull();
    expect(parseBioguideId("B00130")).toBeNull();
    expect(parseBioguideId("../B001309")).toBeNull();
    expect(parseBioguideId("https://example.com")).toBeNull();
    expect(parseBioguideId("")).toBeNull();
  });
});
