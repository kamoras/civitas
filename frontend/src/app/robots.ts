import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

/**
 * /robots.txt, built from SITE_URL so a self-hosted copy names its own
 * sitemap (it was a static file naming the production domain).
 *
 * /api/ is deliberately NOT disallowed: the client-rendered pages load their
 * content from /api/* in the browser, and Googlebot's renderer honours
 * robots.txt for those requests, so blocking /api/ would make Google index
 * those pages as empty shells.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: "/admin" },
    sitemap: `${SITE_URL}/sitemap-index.xml`,
  };
}
