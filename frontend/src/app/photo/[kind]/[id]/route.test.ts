// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";
import type { RemoteImageResult } from "@/lib/remoteImage";

type Opts = { types?: readonly string[]; hosts: readonly string[] };
const fetchRemoteImage = vi.fn<(url: string, opts: Opts) => Promise<RemoteImageResult>>();
vi.mock("@/lib/remoteImage", async (importActual) => ({
  ...(await importActual<typeof import("@/lib/remoteImage")>()),
  fetchRemoteImage: (url: string, opts: Opts) => fetchRemoteImage(url, opts),
}));

const { GET } = await import("./route");
const { parsePhoto, parsePhotoPath } = await import("@/lib/photos");

function get(kind: string, id: string) {
  return GET({} as NextRequest, { params: Promise.resolve({ kind, id }) });
}

/** What the backend's photo-source lookup answers. */
function backendAnswers(status: number, body?: unknown) {
  const backend = vi
    .fn()
    .mockResolvedValue(new Response(body === undefined ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", backend);
  return backend;
}

beforeEach(() => fetchRemoteImage.mockReset());
afterEach(() => vi.unstubAllGlobals());

describe("parsePhoto", () => {
  it("accepts each kind's id", () => {
    expect(parsePhoto("bioguide", "B001309")).toEqual({ kind: "bioguide", id: "B001309" });
    expect(parsePhoto("justice", "jane_q_doe")).toEqual({ kind: "justice", id: "jane_q_doe" });
    expect(parsePhoto("issue", "812")).toEqual({ kind: "issue", id: "812" });
  });

  // The route fetches by this id; anything that isn't one must never reach
  // an outgoing URL.
  it("rejects anything else", () => {
    expect(parsePhoto("bioguide", "b001309")).toBeNull();
    expect(parsePhoto("bioguide", "../B001309")).toBeNull();
    expect(parsePhoto("bioguide", "https://example.com")).toBeNull();
    expect(parsePhoto("justice", "../admin")).toBeNull();
    expect(parsePhoto("issue", "12a")).toBeNull();
    expect(parsePhoto("constructor", "x")).toBeNull();
    expect(parsePhoto("url", "https%3A%2F%2Fexample.com")).toBeNull();
  });

  it("reads the paths the API hands out", () => {
    expect(parsePhotoPath("/photo/bioguide/B001309")).toEqual({ kind: "bioguide", id: "B001309" });
    expect(parsePhotoPath("https://example.com/photo/bioguide/B001309")).toBeNull();
    expect(parsePhotoPath("/photo/bioguide/B001309/x")).toBeNull();
  });
});

// nginx caches this path by status (200 for a day, 404 for ten minutes,
// nothing else), so what each outcome maps to is the contract.
describe("GET /photo/[kind]/[id]", () => {
  it("serves a portrait the backend names, cacheable for a day, raster types only", async () => {
    const backend = backendAnswers(200, {
      url: "https://www.congress.gov/img/member/b001309_200.jpg",
    });
    fetchRemoteImage.mockResolvedValue({
      status: "ok",
      bytes: Buffer.from([1, 2, 3]),
      contentType: "image/jpeg",
    });
    const res = await get("bioguide", "B001309");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/jpeg");
    expect(res.headers.get("cache-control")).toBe("public, max-age=86400");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
    expect(String(backend.mock.calls[0][0])).toMatch(/\/api\/photo-sources\/bioguide\/B001309$/);
    const [url, opts] = fetchRemoteImage.mock.calls[0];
    expect(url).toBe("https://www.congress.gov/img/member/b001309_200.jpg");
    expect(opts.types).toEqual(["image/jpeg", "image/png", "image/webp"]);
    expect(opts.hosts).toEqual(["www.congress.gov", "bioguide.congress.gov"]);
  });

  it("serves bioguide's portrait when that is the source the backend names", async () => {
    backendAnswers(200, { url: "https://bioguide.congress.gov/photo/B001309.jpg" });
    fetchRemoteImage.mockResolvedValue({
      status: "ok",
      bytes: Buffer.from([1]),
      contentType: "image/jpeg",
    });
    const res = await get("bioguide", "B001309");
    expect(res.status).toBe(200);
    expect(fetchRemoteImage.mock.calls[0][0]).toBe(
      "https://bioguide.congress.gov/photo/B001309.jpg"
    );
  });

  it("refuses a member photo off Congress.gov's hosts", async () => {
    backendAnswers(200, { url: "https://api.oyez.org/files/a.png" });
    const res = await get("bioguide", "B001309");
    expect(res.status).toBe(404);
    expect(fetchRemoteImage).not.toHaveBeenCalled();
  });

  it("serves a stored photo from an allowed host the backend names", async () => {
    const backend = backendAnswers(200, { url: "https://api.oyez.org/files/a.png" });
    fetchRemoteImage.mockResolvedValue({
      status: "ok",
      bytes: Buffer.from([4]),
      contentType: "image/png",
    });
    const res = await get("justice", "jane_q_doe");
    expect(res.status).toBe(200);
    expect(String(backend.mock.calls[0][0])).toMatch(/\/api\/photo-sources\/justice\/jane_q_doe$/);
    const [url, opts] = fetchRemoteImage.mock.calls[0];
    expect(url).toBe("https://api.oyez.org/files/a.png");
    // Every redirect hop is held to the same hosts (lib/remoteImage.ts).
    expect(opts.hosts).toEqual(["api.oyez.org"]);
  });

  // An issue's photo URL comes from an RSS item: the route must not fetch
  // from a host the kind's data can't come from, even if the backend says so.
  it.each([
    "https://images.example.org/a.jpg",
    "https://api.oyez.org/files/a.png", // a justice host, not an issue one
    "https://rollcall.com.evil.example/a.jpg",
    "https://rollcall.com:8443/a.jpg",
  ])("refuses a source off the kind's hosts: %s", async (url) => {
    backendAnswers(200, { url });
    const res = await get("issue", "812");
    expect(res.status).toBe(404);
    expect(fetchRemoteImage).not.toHaveBeenCalled();
  });

  it("answers a photo the source doesn't have with a cacheable 404", async () => {
    backendAnswers(200, { url: "https://bioguide.congress.gov/photo/B001309.jpg" });
    fetchRemoteImage.mockResolvedValue({ status: "missing" });
    const res = await get("bioguide", "B001309");
    expect(res.status).toBe(404);
    expect(res.headers.get("cache-control")).toBe("public, max-age=600");
  });

  it("answers a stored photo the backend has no record of with a 404", async () => {
    backendAnswers(404);
    const res = await get("issue", "812");
    expect(res.status).toBe(404);
    expect(fetchRemoteImage).not.toHaveBeenCalled();
  });

  it("never fetches a non-https source", async () => {
    backendAnswers(200, { url: "http://backend:8000/api/admin" });
    const res = await get("issue", "812");
    expect(res.status).toBe(404);
    expect(fetchRemoteImage).not.toHaveBeenCalled();
  });

  // Cached as a 404, one slow request blanked the photo for every visitor
  // for ten minutes.
  it("answers a transient failure with an uncached 502, not a 404", async () => {
    backendAnswers(200, { url: "https://www.congress.gov/img/member/b001309_200.jpg" });
    fetchRemoteImage.mockResolvedValue({ status: "failed" });
    const res = await get("bioguide", "B001309");
    expect(res.status).toBe(502);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("treats the backend being down as transient", async () => {
    backendAnswers(503);
    const res = await get("justice", "jane_q_doe");
    expect(res.status).toBe(502);
  });

  it("never fetches for an invalid id", async () => {
    const backend = backendAnswers(200, { url: "https://bioguide.congress.gov/photo/x.jpg" });
    const res = await get("bioguide", "not-an-id");
    expect(res.status).toBe(404);
    expect(backend).not.toHaveBeenCalled();
    expect(fetchRemoteImage).not.toHaveBeenCalled();
  });
});

const { GET: getWidth } = await import("./[width]/route");
const sharp = (await import("sharp")).default;

describe("GET /photo/[kind]/[id]/[width]", () => {
  function getAt(kind: string, id: string, width: string) {
    return getWidth({} as NextRequest, { params: Promise.resolve({ kind, id, width }) });
  }

  it("scales the photo down to the width asked, as WebP, cacheable for a day", async () => {
    backendAnswers(200, { url: "https://rollcall.com/a.jpg" });
    const source = await sharp({
      create: { width: 1620, height: 1080, channels: 3, background: "#808080" },
    })
      .jpeg()
      .toBuffer();
    fetchRemoteImage.mockResolvedValue({ status: "ok", bytes: source, contentType: "image/jpeg" });
    const res = await getAt("issue", "795", "400");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/webp");
    expect(res.headers.get("cache-control")).toBe("public, max-age=86400");
    const meta = await sharp(Buffer.from(await res.arrayBuffer())).metadata();
    expect([meta.width, meta.height]).toEqual([400, 267]);
  });

  it("never scales a photo up", async () => {
    backendAnswers(200, { url: "https://www.congress.gov/img/member/b001309_200.jpg" });
    const source = await sharp({
      create: { width: 173, height: 216, channels: 3, background: "#808080" },
    })
      .jpeg()
      .toBuffer();
    fetchRemoteImage.mockResolvedValue({ status: "ok", bytes: source, contentType: "image/jpeg" });
    const meta = await sharp(
      Buffer.from(await (await getAt("bioguide", "B001309", "1200")).arrayBuffer())
    ).metadata();
    expect(meta.width).toBe(173);
  });

  // nginx keys the cache on the path: a width off the list must not mint
  // an entry, or fetch anything.
  it("answers any other width 404 without asking the backend", async () => {
    const backend = backendAnswers(200, { url: "https://rollcall.com/a.jpg" });
    for (const width of ["401", "0400", "400.0", "abc", "-1"]) {
      expect((await getAt("issue", "795", width)).status).toBe(404);
    }
    expect(backend).not.toHaveBeenCalled();
  });

  it("treats bytes that don't decode as a failed fetch, never remembered", async () => {
    backendAnswers(200, { url: "https://rollcall.com/a.jpg" });
    fetchRemoteImage.mockResolvedValue({
      status: "ok",
      bytes: Buffer.from("not an image"),
      contentType: "image/jpeg",
    });
    const res = await getAt("issue", "795", "400");
    expect(res.status).toBe(502);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });
});
