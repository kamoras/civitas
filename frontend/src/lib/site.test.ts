import { describe, expect, it } from "vitest";
import {
  DEFAULT_OG_IMAGE,
  FEED_PATH,
  TOPIC_FEEDS,
  absoluteUrl,
  metaDescription,
  HOME_DESCRIPTION,
  HOME_TITLE,
  TITLE_TEMPLATE,
  pageMetadata,
  stateFeed,
} from "./site";

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
    expect(m.openGraph).toMatchObject({
      url: "/bills",
      siteName: "Civitas Research",
      title: "Bills | Civitas Research",
    });
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

  it("advertises the feed on every page — a route's `alternates` replaces the root's", () => {
    const m = pageMetadata({ title: "t", description: "d", path: "/x" });
    expect(m.alternates?.types).toEqual({
      "application/atom+xml": [{ url: FEED_PATH, title: "Civitas Research" }],
    });
  });

  it("puts a page's own feed ahead of the everything feed", () => {
    const m = pageMetadata({
      title: "t",
      description: "d",
      path: "/x",
      feed: stateFeed("ga", "Georgia"),
    });
    expect(m.alternates?.types).toEqual({
      "application/atom+xml": [
        { url: "/feed/states/GA.xml", title: "Civitas Research: Georgia" },
        { url: FEED_PATH, title: "Civitas Research" },
      ],
    });
    expect(
      pageMetadata({ title: "t", description: "d", path: "/x", feed: TOPIC_FEEDS.congress })
        .alternates?.types
    ).toMatchObject({
      "application/atom+xml": [{ url: "/feed/congress.xml" }, { url: FEED_PATH }],
    });
  });

  it("marks missing records noindex", () => {
    expect(
      pageMetadata({ title: "t", description: "d", path: "/x", noindex: true }).robots
    ).toEqual({
      index: false,
      follow: true,
    });
  });
});

it("absoluteUrl joins with or without a leading slash", () => {
  expect(absoluteUrl("/a")).toBe("https://civitas-research.org/a");
  expect(absoluteUrl("a")).toBe("https://civitas-research.org/a");
});

describe("the site name in titles", () => {
  it("appends the full name to every page title but the homepage's", () => {
    expect(TITLE_TEMPLATE).toBe("%s | Civitas Research");
  });

  it("leads the homepage title and description with the full name", () => {
    expect(HOME_TITLE.startsWith("Civitas Research")).toBe(true);
    expect(HOME_DESCRIPTION.startsWith("Civitas Research")).toBe(true);
    expect(HOME_TITLE.length).toBeLessThanOrEqual(65);
    expect(metaDescription(HOME_DESCRIPTION)).toBe(HOME_DESCRIPTION);
  });

  it("keeps a page title whole, whatever characters it holds", () => {
    expect(pageMetadata({ title: "A $& B", description: "d", path: "/x" }).openGraph?.title).toBe(
      "A $& B | Civitas Research"
    );
  });
});
