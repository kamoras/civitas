import { SITE_URL } from "@/lib/site";
import { XML_HEADERS, sitemapIndexXml } from "@/lib/sitemapXml";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// Per request, like /sitemap.xml: `next build` can't reach the backend.
export const dynamic = "force-dynamic";

/** The sitemap index robots.txt names: the main sitemap, then one page per
 * EXPLORE_SITEMAP_PAGE Explore documents (backend api/sitemap.py). The
 * document corpus has no upper bound, and one sitemap file holds at most
 * 50,000 URLs, so they can't simply join /sitemap.xml. */
export async function GET() {
  let pages = 0;
  try {
    const res = await fetch(`${BACKEND}/api/sitemap`, { next: { revalidate: 3600 } });
    if (res.ok) {
      const data = await res.json();
      const count = Number(data?.exploreDocuments);
      const size = Number(data?.explorePageSize);
      if (Number.isFinite(count) && Number.isFinite(size) && size > 0) {
        pages = Math.ceil(count / size);
      }
    }
  } catch {
    pages = 0;
  }
  const locs = [
    `${SITE_URL}/sitemap.xml`,
    ...Array.from({ length: pages }, (_, n) => `${SITE_URL}/sitemaps/explore/${n}`),
  ];
  return new Response(sitemapIndexXml(locs), { headers: XML_HEADERS });
}
