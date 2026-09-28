// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";
import type { RemoteImageResult } from "@/lib/remoteImage";

const fetchRemoteImage =
  vi.fn<(url: string, opts?: { types?: readonly string[] }) => Promise<RemoteImageResult>>();
vi.mock("@/lib/remoteImage", () => ({
  fetchRemoteImage: (url: string, opts?: { types?: readonly string[] }) =>
    fetchRemoteImage(url, opts),
}));

const { GET, parseBioguideId } = await import("./route");

function get(id: string) {
  return GET({} as NextRequest, { params: Promise.resolve({ id }) });
}

beforeEach(() => fetchRemoteImage.mockReset());

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

// nginx caches this path by status (200 for a day, 404 for ten minutes,
// nothing else), so what each outcome maps to is the contract.
describe("GET /photo/bioguide/[id]", () => {
  it("serves the photo, cacheable for a day, asking bioguide for raster types only", async () => {
    fetchRemoteImage.mockResolvedValue({
      status: "ok",
      bytes: Buffer.from([1, 2, 3]),
      contentType: "image/jpeg",
    });
    const res = await get("B001309");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/jpeg");
    expect(res.headers.get("cache-control")).toBe("public, max-age=86400");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
    const [url, opts] = fetchRemoteImage.mock.calls[0];
    expect(url).toBe("https://bioguide.congress.gov/bioguide/photo/B/B001309.jpg");
    expect(opts?.types).toEqual(["image/jpeg", "image/png", "image/webp"]);
  });

  it("answers a photo bioguide doesn't have with a cacheable 404", async () => {
    fetchRemoteImage.mockResolvedValue({ status: "missing" });
    const res = await get("B001309");
    expect(res.status).toBe(404);
    expect(res.headers.get("cache-control")).toBe("public, max-age=600");
  });

  // Cached as a 404, one slow bioguide request blanked the photo for
  // every visitor for ten minutes.
  it("answers a transient failure with an uncached 502, not a 404", async () => {
    fetchRemoteImage.mockResolvedValue({ status: "failed" });
    const res = await get("B001309");
    expect(res.status).toBe(502);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("never fetches for an invalid id", async () => {
    const res = await get("not-an-id");
    expect(res.status).toBe(404);
    expect(fetchRemoteImage).not.toHaveBeenCalled();
  });
});
