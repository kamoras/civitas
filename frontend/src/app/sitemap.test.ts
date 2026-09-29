import { afterEach, describe, expect, it, vi } from "vitest";
import sitemap from "./sitemap";
import { GET as sitemapIndex } from "./sitemap-index.xml/route";
import { GET as explorePage } from "./sitemaps/explore/[page]/route";
import { sitemapIndexXml, urlsetXml } from "@/lib/sitemapXml";

function backend(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const key = Object.keys(routes).find((k) => url.endsWith(k));
      return key
        ? new Response(JSON.stringify(routes[key]))
        : new Response("missing", { status: 404 });
    })
  );
}

afterEach(() => vi.unstubAllGlobals());

const INDEX = { politicians: [], bills: [], issues: [] };

describe("sitemap", () => {
  it("lists each day Congress met, and the week and month pages built from them", async () => {
    // 2026-09-21 is a Monday; the 24th is in the same week.
    backend({ "/api/sitemap": { ...INDEX, congressDays: ["2026-09-21", "2026-09-24"] } });
    const urls = (await sitemap()).map((e) => [
      e.url.replace(/^https:\/\/[^/]+/, ""),
      e.lastModified,
    ]);
    expect(urls).toEqual(
      expect.arrayContaining([
        ["/congress/2026-09-21", "2026-09-21"],
        ["/congress/2026-09-24", "2026-09-24"],
        ["/congress/week/2026-09-21", "2026-09-24"],
        ["/congress/month/2026-09", "2026-09-24"],
      ])
    );
    expect(urls.filter(([u]) => String(u).startsWith("/congress/week/"))).toHaveLength(1);
  });

  it("still serves a backend image that predates congressDays", async () => {
    backend({ "/api/sitemap": INDEX });
    const urls = (await sitemap()).map((e) => e.url);
    expect(urls.some((u) => u.includes("/congress/20"))).toBe(false);
  });
});

describe("sitemap index", () => {
  it("names the main sitemap and one page per Explore chunk", async () => {
    backend({ "/api/sitemap": { ...INDEX, exploreDocuments: 80_001, explorePageSize: 40_000 } });
    const xml = await (await sitemapIndex()).text();
    expect(xml.match(/<loc>/g)).toHaveLength(4);
    expect(xml).toContain("/sitemap.xml</loc>");
    expect(xml).toContain("/sitemaps/explore/2</loc>");
  });

  it("names only the main sitemap when the backend doesn't answer", async () => {
    backend({});
    const xml = await (await sitemapIndex()).text();
    expect(xml.match(/<loc>/g)).toHaveLength(1);
  });
});

describe("Explore sitemap page", () => {
  const call = (page: string) =>
    explorePage(new Request("http://x"), { params: Promise.resolve({ page }) });

  it("lists that page's documents", async () => {
    backend({ "/api/sitemap/explore?page=0": { documents: [{ id: "7", lastmod: "2026-01-02" }] } });
    const res = await call("0");
    expect(res.headers.get("content-type")).toContain("application/xml");
    expect(await res.text()).toContain(
      "<loc>https://civitas-research.org/explore/7</loc><lastmod>2026-01-02</lastmod>"
    );
  });

  it("is a 404, never an empty sitemap, when there is nothing to list", async () => {
    backend({ "/api/sitemap/explore?page=9": { documents: [] } });
    expect((await call("9")).status).toBe(404);
    expect((await call("abc")).status).toBe(404);
  });
});

describe("sitemap XML", () => {
  it("escapes what it quotes", () => {
    expect(urlsetXml([{ loc: "https://x/a?b=1&c=<2>" }])).toContain(
      "<loc>https://x/a?b=1&amp;c=&lt;2&gt;</loc>"
    );
    expect(sitemapIndexXml(["https://x/s&1"])).toContain("<loc>https://x/s&amp;1</loc>");
  });
});
