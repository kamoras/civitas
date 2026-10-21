/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Metadata goes in <head> for every request. Next 16 otherwise streams
  // generateMetadata into <body> for any user agent not on its built-in
  // bot list, and that list lacks the link-preview fetchers of places this
  // site is shared (Bluesky's card fetcher, Mastodon, Telegram). A page
  // whose body rendered on the client (/explore/[id], until 2026-10) then
  // served its description and Open Graph tags after </head>, which the
  // populated Lighthouse audit caught. The cost is the first byte waiting for the
  // metadata fetch the page makes anyway (cached, revalidate 3600).
  htmlLimitedBots: /.*/,
  // The Open Graph images read their fonts from src/app/fonts/og at request
  // time (lib/ogFonts.ts); the standalone server only has the files tracing
  // copies in, and a path built from process.cwd() isn't traced.
  outputFileTracingIncludes: {
    "/api/og": ["./src/app/fonts/og/*.ttf"],
    "/opengraph-image": ["./src/app/fonts/og/*.ttf"],
  },
  // Old paths that still have links out in the world — search results,
  // Bluesky posts, Action Center issues — keep working. Query strings are
  // carried over. /bills moved under /congress (2026-09).
  async redirects() {
    return [
      { source: "/bills", destination: "/congress/bills", permanent: true },
      { source: "/bills/:id", destination: "/congress/bills/:id", permanent: true },
      // Each old directory or ranking address goes straight to its page,
      // never through another redirect. The members directory moved from
      // /politicians to /congress/members (2026-10), which reads the same
      // ?branch=senate|house, ?state= and ?county=; the measures page that
      // was briefly at /congress/members (its own ?chamber=) moved to
      // /congress/members/measures. /scorecard was the directory before
      // /politicians was. The leaderboard ranked members by the composite
      // score, which the site no longer shows (redesign 2026-10).
      // Presidents went to /executive/presidents and justices to /court
      // when Politicians was retired as a section (2026-10).
      ...["/politicians", "/scorecard"].flatMap((source) => [
        {
          source,
          has: [{ type: "query", key: "branch", value: "president" }],
          destination: "/executive/presidents",
          permanent: true,
        },
        {
          source,
          has: [{ type: "query", key: "branch", value: "scotus" }],
          destination: "/court",
          permanent: true,
        },
        { source, destination: "/congress/members", permanent: true },
      ]),
      {
        source: "/congress/members",
        has: [{ type: "query", key: "chamber" }],
        destination: "/congress/members/measures",
        permanent: true,
      },
      {
        source: "/leaderboard",
        has: [{ type: "query", key: "branch", value: "house" }],
        destination: "/congress/members/measures?chamber=house",
        permanent: true,
      },
      {
        source: "/leaderboard",
        has: [{ type: "query", key: "branch", value: "president" }],
        destination: "/executive/presidents",
        permanent: true,
      },
      {
        source: "/leaderboard",
        has: [{ type: "query", key: "branch", value: "scotus" }],
        destination: "/court",
        permanent: true,
      },
      { source: "/leaderboard", destination: "/congress/members/measures", permanent: true },
      // No feed is at /feed itself; the page listing them is (nginx sends
      // /feed/ there too, before Next sees it).
      { source: "/feed", destination: "/feeds", permanent: true },
      // Explore's search page became site search's Documents filter. Next
      // carries the query string over (q, agency, politician_id, comment,
      // type, sort, as the old page read them); document pages
      // (/explore/{key}) are untouched.
      { source: "/explore", destination: "/search?tab=documents", permanent: true },
    ];
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || "http://backend:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
      // The Atom feeds (backend/app/api/feed.py). In production nginx maps
      // these itself and caches them; this is the same mapping for
      // `next dev` and anything else not behind nginx.
      {
        source: "/feed.xml",
        destination: `${backendUrl}/api/feed/all.xml`,
      },
      {
        source: "/feed/:path*",
        destination: `${backendUrl}/api/feed/:path*`,
      },
    ];
  },
};

export default nextConfig;
