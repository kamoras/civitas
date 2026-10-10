// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchRemoteImage } from "./remoteImage";

function respond(body: BodyInit | null, init: ResponseInit) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, init)));
}

afterEach(() => vi.unstubAllGlobals());

const URL_ = "https://bioguide.congress.gov/photo/B001309.jpg";
const HOSTS = ["bioguide.congress.gov"];
const ANY = { hosts: HOSTS };
const JPEG_ONLY = { types: ["image/jpeg"], hosts: HOSTS };

describe("fetchRemoteImage hosts and redirects", () => {
  const jpeg = () =>
    new Response(new Uint8Array([9]), { status: 200, headers: { "content-type": "image/jpeg" } });
  const redirect = (location: string) => new Response(null, { status: 301, headers: { location } });

  it("fetches from an allowed host, following no redirect itself", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jpeg());
    vi.stubGlobal("fetch", fetchMock);
    expect((await fetchRemoteImage(URL_, JPEG_ONLY)).status).toBe("ok");
    expect(fetchMock.mock.calls[0][1].redirect).toBe("manual");
  });

  it("refuses a host off the list without requesting it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jpeg());
    vi.stubGlobal("fetch", fetchMock);
    for (const url of [
      "https://images.example.org/a.jpg",
      "http://bioguide.congress.gov/photo/B001309.jpg",
      "https://bioguide.congress.gov:8443/photo/B001309.jpg",
      "https://user@bioguide.congress.gov/photo/B001309.jpg",
    ]) {
      expect(await fetchRemoteImage(url, JPEG_ONLY)).toEqual({ status: "failed" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("follows a redirect within the allowed hosts", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(redirect("/photo/moved.jpg"))
      .mockResolvedValueOnce(jpeg());
    vi.stubGlobal("fetch", fetchMock);
    expect((await fetchRemoteImage(URL_, JPEG_ONLY)).status).toBe("ok");
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      "https://bioguide.congress.gov/photo/moved.jpg"
    );
  });

  it("refuses a redirect to a host off the list, never requesting it", async () => {
    for (const location of [
      "https://169.254.169.254/latest/meta-data/",
      "http://bioguide.congress.gov/photo/B001309.jpg",
      "https://internal.example/a.jpg",
    ]) {
      const fetchMock = vi.fn().mockResolvedValueOnce(redirect(location));
      vi.stubGlobal("fetch", fetchMock);
      expect(await fetchRemoteImage(URL_, JPEG_ONLY)).toEqual({ status: "failed" });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
  });

  it("gives up after a few redirects", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => redirect("/photo/again.jpg"))
    );
    expect(await fetchRemoteImage(URL_, JPEG_ONLY)).toEqual({ status: "failed" });
  });
});

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
    expect(await fetchRemoteImage(URL_, ANY)).toEqual({ status: "missing" });
  });

  // The bug this guards: a timeout or a Cloudflare challenge came back as
  // "no photo", which the route served as a 404 and nginx then cached for
  // ten minutes. Transient failures are "failed", which the route maps to an
  // uncached 502.
  it("reports a challenge, a server error or a timeout as failed, not missing", async () => {
    respond("challenge", { status: 403, headers: { "content-type": "text/html" } });
    expect(await fetchRemoteImage(URL_, ANY)).toEqual({ status: "failed" });
    respond(null, { status: 503 });
    expect(await fetchRemoteImage(URL_, ANY)).toEqual({ status: "failed" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("timed out", "TimeoutError"))
    );
    expect(await fetchRemoteImage(URL_, ANY)).toEqual({ status: "failed" });
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
