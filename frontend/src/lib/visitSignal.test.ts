import { describe, expect, it } from "vitest";
import { visitSignal } from "./visitSignal";

const signal = (headers: Record<string, string>) => visitSignal(new Headers(headers));

describe("visitSignal", () => {
  it("holds a browser's page load", () => {
    expect(signal({ "sec-fetch-dest": "document", "sec-fetch-site": "none" })).toBe("page");
  });

  it("ignores a speculative page fetch", () => {
    expect(signal({ "sec-fetch-dest": "document", "sec-purpose": "prefetch;prerender" })).toBe(
      null
    );
    expect(signal({ "sec-fetch-dest": "document", purpose: "prefetch" })).toBe(null);
  });

  it("ignores a client that sends no fetch metadata", () => {
    expect(signal({})).toBe(null);
  });

  // What a Next link prefetch or navigation looks like by the time the
  // proxy sees it (RSC headers stripped; recorded under `next build`).
  it("reads the page's own fetch as the page running", () => {
    expect(
      signal({
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
      })
    ).toBe("router");
  });

  it("ignores another site's fetch and the page's images", () => {
    expect(signal({ "sec-fetch-dest": "empty", "sec-fetch-site": "cross-site" })).toBe(null);
    expect(signal({ "sec-fetch-dest": "image", "sec-fetch-site": "same-origin" })).toBe(null);
  });
});
