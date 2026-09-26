import type { Metadata } from "next";

/**
 * The site's public origin and name, for everything a search engine or a
 * link unfurler reads: canonical URLs, sitemap entries, Open Graph urls,
 * JSON-LD. One copy — it used to be a `const SITE` repeated per route.
 *
 * Canonical URLs are set per route, never in the root layout. `alternates`
 * is inherited by every nested segment that doesn't set its own, so a
 * canonical on the root would tell Google every page on the site is a
 * duplicate of the homepage. The same holds for a section layout
 * (`/politicians`, `/explore`...): any dynamic child under it must set its
 * own canonical, or it inherits the section's.
 */
export const SITE_URL = "https://civitas-research.org";
export const SITE_NAME = "Civitas";
export const BSKY_PROFILE_URL = "https://bsky.app/profile/civitas-research.org";
export const GITHUB_REPO_URL = "https://github.com/kamoras/civitas";

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
  alt: "Civitas — public-record scorecards for Congress",
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
 * " — Civitas". Pass `absoluteTitle` to opt out (the homepage).
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
}: {
  title: string;
  description: string;
  path: string;
  images?: OgImage[];
  type?: "website" | "article" | "profile";
  absoluteTitle?: boolean;
  noindex?: boolean;
}): Metadata {
  const fullTitle = absoluteTitle ? title : `${title} — ${SITE_NAME}`;
  const desc = metaDescription(description);
  return {
    title: absoluteTitle ? { absolute: title } : title,
    description: desc,
    alternates: { canonical: path },
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
 * The homepage's title and description, which are also the site-wide
 * fallbacks. They lead with what people type into a search box — the old
 * title, "CIVITAS // PUBLIC RECORD", matched no query anyone makes.
 */
export const HOME_TITLE = "Civitas — Congress Scorecards, Voting Records & Campaign Finance";
export const HOME_DESCRIPTION =
  "Nonpartisan scorecards for every senator, representative, president, and Supreme Court justice: voting records, donors, PAC money, and bills from public data.";
