import type { Metadata } from "next";

/**
 * The site's public origin and name, for everything a search engine or a
 * link unfurler reads: canonical URLs, sitemap entries, Open Graph urls,
 * JSON-LD. One copy — it used to be a `const SITE` repeated per route.
 *
 * Canonical URLs are set per route, never in the root layout. `alternates`
 * is inherited by every nested segment that doesn't set its own, so a
 * canonical on the root would tell Google every page on the site is a
 * duplicate of the homepage. The same holds for a section layout: a
 * section whose index page is a client component (`/politicians`,
 * `/congress/bills`, `/elections`, `/explore`) keeps that page's metadata in
 * a layout inside an `(index)` route group, wrapping the index alone. A
 * record page beneath can set its own, but its 404 can't (Next resolves a
 * not-found page's metadata from the layouts above it only), so a section
 * layout's title and canonical used to label every missing member, bill
 * and state as the directory.
 */
export const SITE_URL = "https://civitas-research.org";
/**
 * The site's name everywhere a search engine reads one: the title template,
 * og:site_name, application-name, the manifest, the WebSite and
 * Organization JSON-LD. "Civitas" alone is shared by a UK think tank, a
 * U.S. university institute, an oil company, a dictionary word and more,
 * so the name search engines are given is the full one, matching the
 * domain. Google picks the site name it shows from the WebSite structured
 * data, the title and og:site_name, and wants them to agree
 * (developers.google.com/search/docs/appearance/site-names). The on-page
 * mark stays "CIVITAS"; this is the name, not the logo.
 */
export const SITE_NAME = "Civitas Research";
/** The root layout's title template: every page but the homepage. */
export const TITLE_TEMPLATE = `%s | ${SITE_NAME}`;
/** Other names the site goes by, for WebSite/Organization `alternateName`. */
export const SITE_ALTERNATE_NAMES = ["Civitas", "civitas-research.org"];
/** Civitas's Bluesky account, the only social account it has. */
export const BSKY_HANDLE = "civitas-research.org";
export const BSKY_PROFILE_URL = `https://bsky.app/profile/${BSKY_HANDLE}`;
export const GITHUB_REPO_URL = "https://github.com/kamoras/civitas";

/** Everything Civitas publishes, as Atom (backend/app/api/feed.py). Topic
 *  and per-state feeds are listed on /feeds. */
export const FEED_PATH = "/feed.xml";

/**
 * The feed, advertised in every page's <head> so a feed reader given any
 * Civitas URL finds it. Part of `alternates`, which Next merges shallowly:
 * a route that sets `alternates` (every route, through `pageMetadata`)
 * drops its parent's, so the root layout and `pageMetadata` both carry it.
 */
export const FEED_ALTERNATES = {
  "application/atom+xml": [{ url: FEED_PATH, title: "Civitas" }],
};

export interface Feed {
  url: string;
  title: string;
}

/** The topic feeds a page can name as its own (backend/app/broadcast.py
 *  FEEDS; paths from api/feed.py feed_path). */
export const TOPIC_FEEDS = {
  issues: { url: "/feed/issues.xml", title: "Civitas: Action Center" },
  congress: { url: "/feed/congress.xml", title: "Civitas: Congress" },
  elections: { url: "/feed/elections.xml", title: "Civitas: Elections" },
  trends: { url: "/feed/trends.xml", title: "Civitas: On the record" },
  new: { url: "/feed/new.xml", title: "Civitas: What's new" },
} satisfies Record<string, Feed>;

/** A state's feed: its races and its members of Congress (api/feed.py
 *  state_feed_path, titled as the feed titles itself). */
export function stateFeed(code: string, name: string): Feed {
  return { url: `/feed/states/${code.toUpperCase()}.xml`, title: `Civitas: ${name}` };
}

/**
 * `alternates.types` for a page: its own, most specific feed first (a reader
 * or browser extension given the page commonly offers the first link it
 * finds), then everything.
 */
export function feedAlternates(feed?: Feed) {
  return feed
    ? { "application/atom+xml": [feed, ...FEED_ALTERNATES["application/atom+xml"]] }
    : FEED_ALTERNATES;
}

/** Absolute URL for a site path ("/bills/S.1" → "https://…/bills/S.1"). */
export function absoluteUrl(path: string): string {
  return `${SITE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * Meta descriptions past ~160 characters are cut by the result page anyway;
 * cutting them ourselves at a word boundary keeps the ellipsis honest
 * instead of landing mid-word.
 */
export function metaDescription(text: string, max = 160): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max - 1);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).replace(/[\s,;:.—-]+$/, "")}…`;
}

type OgImage = { url: string; width?: number; height?: number; alt?: string };

/** The root `app/opengraph-image.tsx` card, by its route. */
export const DEFAULT_OG_IMAGE: OgImage = {
  url: "/opengraph-image",
  width: 1200,
  height: 630,
  alt: "Civitas Research: public-record scorecards for Congress",
};

/**
 * Everything a route needs a search engine / unfurler to read, in one shape.
 *
 * Next merges metadata SHALLOWLY per top-level key: a route that sets
 * `openGraph` at all replaces its parent's entire `openGraph` object, which
 * is how most routes here ended up with no og:site_name and no og:url. This
 * builds the complete set every time instead.
 *
 * `title` is the page's own title; the root layout's template appends
 * " | Civitas Research". Pass `absoluteTitle` to opt out (the homepage).
 *
 * `images` defaults to the site card. It has to be explicit: the root
 * `opengraph-image` file is NOT inherited by a route that sets `openGraph`
 * itself (verified against `next build` — /about, /action and every bill
 * page rendered with no og:image at all).
 */
export function pageMetadata({
  title,
  description,
  path,
  images = [DEFAULT_OG_IMAGE],
  type = "website",
  absoluteTitle = false,
  noindex = false,
  feed,
}: {
  title: string;
  description: string;
  path: string;
  images?: OgImage[];
  type?: "website" | "article" | "profile";
  absoluteTitle?: boolean;
  noindex?: boolean;
  /** The page's own feed (TOPIC_FEEDS, stateFeed), advertised ahead of
   *  the everything feed. */
  feed?: Feed;
}): Metadata {
  const fullTitle = absoluteTitle ? title : TITLE_TEMPLATE.replace("%s", () => title);
  const desc = metaDescription(description);
  return {
    title: absoluteTitle ? { absolute: title } : title,
    description: desc,
    alternates: { canonical: path, types: feedAlternates(feed) },
    openGraph: {
      title: fullTitle,
      description: desc,
      url: path,
      siteName: SITE_NAME,
      locale: "en_US",
      type,
      images,
    },
    twitter: {
      card: "summary_large_image",
      title: fullTitle,
      description: desc,
      images,
    },
    ...(noindex ? { robots: { index: false, follow: true } } : {}),
  };
}

/**
 * A missing record's head, for a record route's generateMetadata: its own
 * title, noindex, and no canonical or og:url, since a 404 is not a page to
 * point at (pageMetadata would declare one).
 *
 * A route's notFound() discards its page's generateMetadata (Next resolves
 * a not-found page's head from the layouts above it), so each record route
 * also has a layout that sets only the 404's title: on a found record the
 * page's own title replaces it, and the layout sets no robots or canonical
 * that a found page would inherit.
 */
export function notFoundMetadata(title: string, description: string): Metadata {
  return { title, description, robots: { index: false, follow: true } };
}

/**
 * The homepage's title and description, which are also the site-wide
 * fallbacks. They lead with the full name (a search for the brand has to
 * find it among every other "Civitas") and then say what the site is, in
 * the words people search with — the old title, "CIVITAS // PUBLIC
 * RECORD", matched no query anyone makes. Both are kept under what a
 * result page shows (~60 and 160 characters).
 */
export const HOME_TITLE = "Civitas Research: Congress Scorecards & Political Transparency";
export const HOME_DESCRIPTION =
  "Civitas Research is a nonpartisan public record of the U.S. government: scorecards for every member of Congress, president and justice, from votes and donors.";
