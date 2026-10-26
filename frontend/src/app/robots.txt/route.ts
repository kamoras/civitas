import { SITE_URL } from "@/lib/site";

/**
 * /robots.txt, built from SITE_URL so a self-hosted copy names its own
 * sitemap (it was a static file naming the production domain). A route
 * handler, not Next's robots.ts, because MetadataRoute.Robots can't carry
 * the Content-Signal line (contentsignals.org): every use is allowed,
 * search, AI answers that cite a page and AI training alike, since the
 * site's purpose is that people find the public record.
 *
 * /api/ is deliberately NOT disallowed: the client-rendered pages load their
 * content from /api/* in the browser, and Googlebot's renderer honours
 * robots.txt for those requests, so blocking /api/ would make Google index
 * those pages as empty shells.
 */
export function robotsTxt(): string {
  return [
    "User-Agent: *",
    "Content-Signal: search=yes, ai-input=yes, ai-train=yes",
    "Allow: /",
    "Disallow: /admin",
    "",
    `Sitemap: ${SITE_URL}/sitemap-index.xml`,
    "",
  ].join("\n");
}

export function GET(): Response {
  return new Response(robotsTxt(), { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
