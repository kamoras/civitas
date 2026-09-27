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
  // /bills moved under /congress (2026-09): old links — search results,
  // Bluesky posts, Action Center issues — keep working.
  async redirects() {
    return [
      { source: "/bills", destination: "/congress/bills", permanent: true },
      { source: "/bills/:id", destination: "/congress/bills/:id", permanent: true },
    ];
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || "http://backend:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
