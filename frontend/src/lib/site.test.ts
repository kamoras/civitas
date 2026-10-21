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
  memberFeed,
  billFeed,
  agencyFeed,
  monitorFeed,
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

  it("gives a section page its own card, described in its alt text", () => {
    const m = pageMetadata({ title: "t", description: "d", path: "/elections/calendar" });
    const [image] = m.openGraph?.images as { url: string; alt: string }[];
    expect(image.url).toBe(`/api/og?page=%2Felections%2Fcalendar&v=${process.env.OG_CARD_VERSION}`);
    expect(image.alt).toMatch(/^Elections: Election calendar\. /);
    expect(m.twitter?.images).toEqual(m.openGraph?.images);
  });

  it("keeps an explicit card over the section card", () => {
    const own = [{ url: "/api/og?x=1", alt: "own" }];
    const m = pageMetadata({ title: "t", description: "d", path: "/congress", images: own });
    expect(m.openGraph?.images).toEqual(own);
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

  it("names one followed thing's feed at the path the backend serves it on", () => {
    // backend api/feed.py member_feed_path, bill_feed_path, agency_feed_path, monitor_feed_path
    expect(memberFeed("jane-doe", "Jane Doe")).toEqual({
      url: "/feed/members/jane-doe.xml",
      title: "Civitas Research: Jane Doe",
    });
    expect(billFeed(119, "HR.1", "H.R. 1").url).toBe("/feed/bills/119/HR.1.xml");
    expect(agencyFeed("agriculture-department", "Agriculture Department").url).toBe(
      "/feed/agencies/agriculture-department.xml"
    );
    expect(monitorFeed("a story", "A Story").url).toBe("/feed/monitors/a%20story.xml");
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
