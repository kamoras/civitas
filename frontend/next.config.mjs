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
  // /scorecard became /politicians (same ?branch= and ?state=), and posts
  // already published on Bluesky still link to it. The query string is
  // carried over.
  async redirects() {
    return [{ source: "/scorecard", destination: "/politicians", permanent: true }];
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
