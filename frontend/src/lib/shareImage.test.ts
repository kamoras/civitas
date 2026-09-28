import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bioguideIdFromPhotoUrl, bioguidePhotoUrl } from "./bioguide";
import {
  captureImageData,
  captureScale,
  displayUrl,
  proxiedImageUrl,
  sectionUrl,
  shareFileName,
} from "./shareImage";

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

describe("captureScale", () => {
  it("captures at the screen's ratio, at least 2x and at most 3x", () => {
    expect(captureScale(400, 600, 1)).toBe(2);
    expect(captureScale(400, 600, 3)).toBe(3);
    expect(captureScale(400, 600, 4)).toBe(3);
  });

  // The bug: a long contest drawer on an iPhone (3x) exceeded Safari's
  // 16,777,216-pixel canvas limit, and the capture failed outright.
  it("lowers the ratio so a tall section still fits a canvas every browser allocates", () => {
    const scale = captureScale(390, 6000, 3);
    expect(scale).toBeLessThan(3);
    expect((390 + 40) * scale * (6000 + 260) * scale).toBeLessThanOrEqual(16_777_216);
    expect((6000 + 260) * scale).toBeLessThanOrEqual(16_000);
  });
});

describe("captureImageData", () => {
  const fetchSpy = vi.fn();
  beforeEach(() => {
    fetchSpy.mockReset();
    vi.stubGlobal("fetch", fetchSpy);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lets same-origin and inline images load normally", async () => {
    expect(await captureImageData(`${window.location.origin}/_next/static/a.png`)).toBe(false);
    expect(await captureImageData("/icon.svg")).toBe(false);
    expect(await captureImageData("data:image/png;base64,AAAA")).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("reads a member photo through the site's own route", async () => {
    // A minimal Response: jsdom's Blob can't be handed to Node's Response.
    fetchSpy.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["jpg"], { type: "image/jpeg" }),
    });
    const data = await captureImageData(
      "https://bioguide.congress.gov/bioguide/photo/B/B001309.jpg"
    );
    expect(fetchSpy).toHaveBeenCalledWith("/photo/bioguide/B001309");
    expect(data).toMatch(/^data:image\/jpeg;base64,/);
  });

  // A capture must never make the visitor's browser request a third-party
  // host (AGENTS.md §8) — a publisher's image is left blank, unfetched.
  it("leaves any other third-party image blank without requesting it", async () => {
    const data = await captureImageData("https://images.example-news.com/photo.jpg");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(data).toMatch(/^data:image\/png;base64,/);
  });
});

describe("bioguide photo URLs", () => {
  it("round-trips an id through the URL the API serves", () => {
    expect(bioguideIdFromPhotoUrl(bioguidePhotoUrl("B001309"))).toBe("B001309");
  });

  it("rejects a URL whose folder letter doesn't match the id", () => {
    expect(
      bioguideIdFromPhotoUrl("https://bioguide.congress.gov/bioguide/photo/Z/B001309.jpg")
    ).toBeNull();
  });
});
