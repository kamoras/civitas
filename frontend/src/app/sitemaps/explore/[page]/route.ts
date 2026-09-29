import { SITE_URL } from "@/lib/site";
import { XML_HEADERS, urlsetXml } from "@/lib/sitemapXml";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

export const dynamic = "force-dynamic";

/** One page of Explore documents (/explore/{id}), listed by
 * /sitemap-index.xml. A page the backend can't answer is a 404, not an
 * empty sitemap: an empty one would tell a crawler those documents are
 * gone. */
export async function GET(_req: Request, { params }: { params: Promise<{ page: string }> }) {
  const { page } = await params;
  if (!/^\d{1,5}$/.test(page)) return new Response("Not found", { status: 404 });
  try {
    const res = await fetch(`${BACKEND}/api/sitemap/explore?page=${page}`, {
      next: { revalidate: 3600 },
    });
    if (!res.ok) return new Response("Not found", { status: 404 });
    const data = await res.json();
    const docs: { id: string; lastmod: string | null }[] = Array.isArray(data?.documents)
      ? data.documents
      : [];
    if (docs.length === 0) return new Response("Not found", { status: 404 });
    return new Response(
      urlsetXml(
        docs.map((d) => ({
          loc: `${SITE_URL}/explore/${encodeURIComponent(d.id)}`,
          lastmod: d.lastmod,
        }))
      ),
      { headers: XML_HEADERS }
    );
  } catch {
    return new Response("Not found", { status: 404 });
  }
}
