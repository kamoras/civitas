/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Metadata goes in <head> for every request. Next 16 otherwise streams
  // generateMetadata into <body> for any user agent not on its built-in
  // bot list, and that list lacks the link-preview fetchers of places this
  // site is shared (Bluesky's card fetcher, Mastodon, Telegram). Pages
  // whose body renders on the client (/explore/[id]) then served their
  // description and Open Graph tags after </head>, which the populated
  // Lighthouse audit caught. The cost is the first byte waiting for the
  // metadata fetch the page makes anyway (cached, revalidate 3600).
  htmlLimitedBots: /.*/,
  // Old paths that still have links out in the world — search results,
  // Bluesky posts, Action Center issues — keep working. Query strings are
  // carried over. /bills moved under /congress (2026-09); /scorecard became
  // /politicians (same ?branch= and ?state=).
  async redirects() {
    return [
      { source: "/bills", destination: "/congress/bills", permanent: true },
      { source: "/bills/:id", destination: "/congress/bills/:id", permanent: true },
      { source: "/scorecard", destination: "/politicians", permanent: true },
      // No feed is at /feed itself; the page listing them is (nginx sends
      // /feed/ there too, before Next sees it).
      { source: "/feed", destination: "/feeds", permanent: true },
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
