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

describe("issue card render", () => {
  it("renders an issue card as a PNG", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/api/action/issues/i0000abcd"))
          return Response.json({
            publicId: "i0000abcd",
            title: "Agency sues a state",
            summary: "The agency sued Thursday.",
            summarySource: "Outlet B",
            date: "2026-10-15",
            status: "confirmed",
            sourceNames: ["Outlet A", "Outlet B"],
            imageUrl: null,
          });
        throw new Error("offline");
      })
    );
    const res = await GET(new NextRequest("http://localhost/api/og?issue=i0000abcd"));
    expect(res.headers.get("content-type")).toBe("image/png");
  });
});

describe("state ballot card render", () => {
  it("renders a card whose tiles are sentences, and reads no version parameter", async () => {
    // Wisconsin on 2026-10-21: no Senate seat up, measures not read yet.
    const ballot = {
      state: "WI",
      stateName: "Wisconsin",
      cycleYear: 2026,
      electionDate: "2026-11-03",
      senateRaces: [],
      nextSenateElection: 2028,
      houseRaces: [{}, {}],
      measures: [],
      measureCoverage: { status: "not_yet_covered" },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/api/elections/states/WI")) return Response.json(ballot);
        throw new Error("offline");
      })
    );
    const res = await GET(new NextRequest("http://localhost/api/og?state=WI&v=0123456789"));
    const body = new Uint8Array(await res.arrayBuffer());
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/png");
    expect(Array.from(body.slice(1, 4))).toEqual([0x50, 0x4e, 0x47]);
  });
});
