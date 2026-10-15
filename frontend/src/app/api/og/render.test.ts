// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "./route";

afterEach(() => vi.unstubAllGlobals());

// Renders through next/og itself, offline: the font fetch fails and the
// route falls back to the default face, as it does in production.
describe("record card render", () => {
  it("renders a vote card as a PNG", async () => {
    const card = {
      kind: "vote",
      key: "senate/119/2/123",
      eyebrow: "Senate roll call 123",
      headline: "On Passage",
      date: "2026-10-09",
      path: "/x",
      source_name: "Senate.gov",
      as_of: "2026-10-10",
      lines: [],
      tally: [
        {
          position: "yea",
          label: "Yea",
          total: 2,
          parties: [{ party: "R", name: "Republican", count: 2 }],
        },
      ],
      source: "Source: Senate.gov",
      alt: "",
    };
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("/api/cards/vote:senate/119/2/123?title=1"))
        return Response.json(card);
      throw new Error("offline");
    });
    vi.stubGlobal("fetch", fetchMock);
    const res = await GET(new NextRequest("http://localhost/api/og?vote=senate/119/2/123"));
    const body = new Uint8Array(await res.arrayBuffer());
    expect(res.headers.get("content-type")).toBe("image/png");
    expect(Array.from(body.slice(1, 4))).toEqual([0x50, 0x4e, 0x47]);
  });

  it("falls back to the generic card when the record is unknown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/api/cards/")) return new Response("", { status: 404 });
        throw new Error("offline");
      })
    );
    const res = await GET(new NextRequest("http://localhost/api/og?bill=119/S.1"));
    expect(res.headers.get("content-type")).toBe("image/png");
    expect(res.headers.get("cache-control")).toBe("public, max-age=300");
  });
});
