// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchRemoteImage } from "./remoteImage";

function respond(body: BodyInit | null, init: ResponseInit) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, init)));
}

afterEach(() => vi.unstubAllGlobals());

const URL_ = "https://bioguide.congress.gov/bioguide/photo/B/B001309.jpg";
const JPEG_ONLY = { types: ["image/jpeg"] };

describe("fetchRemoteImage", () => {
  it("returns the bytes and the bare media type", async () => {
    respond(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "content-type": "image/jpeg; charset=utf-8" },
    });
    const r = await fetchRemoteImage(URL_, JPEG_ONLY);
    expect(r).toEqual({ status: "ok", bytes: Buffer.from([1, 2, 3]), contentType: "image/jpeg" });
  });

  it("reports an image the host says doesn't exist as missing", async () => {
    respond(null, { status: 404 });
    expect(await fetchRemoteImage(URL_)).toEqual({ status: "missing" });
  });

  // The bug this guards: a timeout or a Cloudflare challenge came back as
  // "no photo", which the route served as a 404 and nginx then cached for
  // ten minutes. Transient failures are "failed", which the route maps to an
  // uncached 502.
  it("reports a challenge, a server error or a timeout as failed, not missing", async () => {
    respond("challenge", { status: 403, headers: { "content-type": "text/html" } });
    expect(await fetchRemoteImage(URL_)).toEqual({ status: "failed" });
    respond(null, { status: 503 });
    expect(await fetchRemoteImage(URL_)).toEqual({ status: "failed" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("timed out", "TimeoutError"))
    );
    expect(await fetchRemoteImage(URL_)).toEqual({ status: "failed" });
  });

  it("refuses a type outside the allowed list (an SVG for a portrait)", async () => {
    respond("<svg/>", { status: 200, headers: { "content-type": "image/svg+xml" } });
    expect(await fetchRemoteImage(URL_, JPEG_ONLY)).toEqual({ status: "failed" });
  });

  it("stops reading a chunked body past the size cap", async () => {
    const chunk = new Uint8Array(1024 * 1024);
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        sent += 1;
        if (sent > 50) controller.close();
        else controller.enqueue(chunk);
      },
    });
    respond(stream, { status: 200, headers: { "content-type": "image/jpeg" } });
    expect(await fetchRemoteImage(URL_, JPEG_ONLY)).toEqual({ status: "failed" });
    expect(sent).toBeLessThan(10);
  });
});
