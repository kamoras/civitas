import { describe, expect, it } from "vitest";
import { isPageLoad } from "./pageLoad";

describe("isPageLoad", () => {
  it("counts a browser opening a page", () => {
    expect(
      isPageLoad(new Headers({ "sec-fetch-dest": "document", "sec-fetch-mode": "navigate" }))
    ).toBe(true);
  });
  it("counts a client that sends no fetch metadata", () => {
    expect(isPageLoad(new Headers())).toBe(true);
  });
  it("does not count the router fetching a page's data", () => {
    // As the middleware sees a Next prefetch or navigation fetch under `next build`.
    expect(
      isPageLoad(
        new Headers({ "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "next-url": "/" })
      )
    ).toBe(false);
  });
  it("does not count the browser's own prefetch", () => {
    expect(isPageLoad(new Headers({ "sec-purpose": "prefetch" }))).toBe(false);
    expect(
      isPageLoad(new Headers({ "sec-fetch-dest": "document", "sec-purpose": "prefetch;prerender" }))
    ).toBe(false);
    expect(isPageLoad(new Headers({ purpose: "prefetch" }))).toBe(false);
  });
});
