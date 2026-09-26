import { describe, expect, it } from "vitest";
import { DEFAULT_OG_IMAGE, absoluteUrl, metaDescription, pageMetadata } from "./site";

describe("metaDescription", () => {
  it("leaves short text alone, collapsing whitespace", () => {
    expect(metaDescription("  a   b \n c ")).toBe("a b c");
  });

  it("cuts long text at a word boundary with an ellipsis", () => {
    const out = metaDescription("word ".repeat(60));
    expect(out.length).toBeLessThanOrEqual(160);
    expect(out.endsWith("word…")).toBe(true);
  });
});

describe("pageMetadata", () => {
  it("sets canonical, og:url and og:site_name together", () => {
    const m = pageMetadata({ title: "Bills", description: "d", path: "/bills" });
    expect(m.alternates?.canonical).toBe("/bills");
    expect(m.openGraph).toMatchObject({ url: "/bills", siteName: "Civitas", title: "Bills — Civitas" });
    expect(m.title).toBe("Bills");
    expect(m.robots).toBeUndefined();
  });

  it("opts out of the title template for the homepage", () => {
    const m = pageMetadata({ title: "Home", description: "d", path: "/", absoluteTitle: true });
    expect(m.title).toEqual({ absolute: "Home" });
    expect(m.openGraph?.title).toBe("Home");
  });

  it("always carries an og:image — the root file-based one is not inherited", () => {
    const m = pageMetadata({ title: "t", description: "d", path: "/x" });
    expect(m.openGraph?.images).toEqual([DEFAULT_OG_IMAGE]);
    expect(m.twitter?.images).toEqual([DEFAULT_OG_IMAGE]);
  });

  it("marks missing records noindex", () => {
    expect(pageMetadata({ title: "t", description: "d", path: "/x", noindex: true }).robots).toEqual({
      index: false,
      follow: true,
    });
  });
});

it("absoluteUrl joins with or without a leading slash", () => {
  expect(absoluteUrl("/a")).toBe("https://civitas-research.org/a");
  expect(absoluteUrl("a")).toBe("https://civitas-research.org/a");
});
