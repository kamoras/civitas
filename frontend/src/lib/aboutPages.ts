/**
 * The About section's chapters, in reading order.
 *
 * /about used to be one 10,000-word page — roughly 33 screens, every other
 * page on the site under ~1,000 — and two passes at making it readable
 * (plain-language "In short" lead-ins in 2026-07, collapsing every section
 * in 2026-08) each helped a little and left the wall intact. In 2026-09 it
 * was rebuilt as a short overview at /about plus one chapter per subject,
 * so a reader who wants to know how Constituent Alignment works reads a page
 * about Constituent Alignment, not a page about everything.
 *
 * One list drives the overview's chapter cards, every chapter's navigation,
 * the previous/next links and the sitemap, so a chapter added here appears
 * everywhere it should.
 */
export interface AboutChapter {
  href: string;
  /** Short name for navigation. */
  title: string;
  /** One line for the overview's chapter cards. */
  blurb: string;
}

export const ABOUT_OVERVIEW_HREF = "/about";

export const ABOUT_CHAPTERS: readonly AboutChapter[] = [
  {
    href: "/about/scores",
    title: "How Congress is scored",
    blurb:
      "The three parts of every senator's and representative's score, what moves each one, and how we checked them.",
  },
  {
    href: "/about/presidents-and-justices",
    title: "Presidents & justices",
    blurb:
      "How presidents are scored, why some scores read N/A, and why Supreme Court justices are not scored.",
  },
  {
    href: "/about/elections",
    title: "Elections & ballots",
    blurb:
      "What a state ballot page shows, what it leaves out, and why ballot measures are quoted word for word.",
  },
  {
    href: "/about/news",
    title: "News & Congress reports",
    blurb:
      "How the Action Center picks stories and keeps them in the sources' own words, and how corrections work.",
  },
  {
    href: "/about/data",
    title: "Data, AI & how it runs",
    blurb:
      "Every data source, exactly where AI is and isn't used, what we record about visits, and the single small computer it all runs on.",
  },
  {
    href: "/about/limitations",
    title: "Known limitations",
    blurb:
      "What the scores can't tell you, stated plainly, with the reason each gap is still open.",
  },
  {
    href: "/about/references",
    title: "References",
    blurb: "The research every method on these pages is built on.",
  },
];

/**
 * Old /about fragment -> where that content lives now.
 *
 * Fragments never reach the server, so these can't be redirects; the
 * overview reads the hash on load and forwards (see LegacyAboutAnchor).
 * They are here because each was a real id on the old page and any of them
 * may have been shared: /about#known-limitations in particular was linked
 * from outside the site.
 */
export const LEGACY_ABOUT_ANCHORS: Readonly<Record<string, string>> = {
  "known-limitations": "/about/limitations",
  "state-ballots": "/about/elections",
  retractions: "/about/news#retractions",
  congress: "/about/news#congress-reports",
};

/** Where a legacy /about#fragment should go, or null to stay on the overview. */
export function legacyAboutTarget(
  hash: string,
  refIds: Readonly<Record<string, string>>
): string | null {
  const key = hash.replace(/^#/, "");
  if (!key) return null;
  if (key in LEGACY_ABOUT_ANCHORS) return LEGACY_ABOUT_ANCHORS[key];
  // Numbered citation anchors (#ref-5) from the old single-page reference list.
  const ref = /^ref-(\d+)$/.exec(key);
  if (ref && refIds[ref[1]]) return `/about/references#${refIds[ref[1]]}`;
  return null;
}

/** The chapters either side of `href`, for previous/next links. */
export function adjacentChapters(href: string): {
  prev: AboutChapter | null;
  next: AboutChapter | null;
} {
  const i = ABOUT_CHAPTERS.findIndex((c) => c.href === href);
  if (i < 0) return { prev: null, next: null };
  return {
    prev: i > 0 ? ABOUT_CHAPTERS[i - 1] : null,
    next: i < ABOUT_CHAPTERS.length - 1 ? ABOUT_CHAPTERS[i + 1] : null,
  };
}
