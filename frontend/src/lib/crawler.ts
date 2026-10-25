import { SITE_URL } from "@/lib/site";
import type { CrawlerFacts } from "@/types/crawler";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

/** The crawler's facts for /about/crawler (GET /api/crawler), or null when
 *  the backend can't be asked (as under `next build`). Re-read every five
 *  minutes, the page's `revalidate`. */
export async function fetchCrawlerFacts(): Promise<CrawlerFacts | null> {
  try {
    const res = await fetch(`${BACKEND}/api/crawler`, { next: { revalidate: 300 } });
    if (!res.ok) return null;
    return (await res.json()) as CrawlerFacts;
  } catch {
    return null;
  }
}

/** The User-Agent the frontend's own server-side fetches send (the photo
 *  route, the share cards): the backend's (app/contact.py), built from the
 *  same SITE_URL and CONTACT_EMAIL. Server only: CONTACT_EMAIL is not a
 *  public variable, so a browser bundle would leave it out. */
export function crawlerUserAgent(contact = process.env.CONTACT_EMAIL ?? ""): string {
  const email = /^[^\s@()<>;,]+@[^\s@()<>;,]+\.[^\s@()<>;,]+$/.test(contact.trim())
    ? contact.trim()
    : "";
  return `Civitas/1.0 (+${SITE_URL}/about/crawler${email ? `; ${email}` : ""})`;
}
