import type { MetadataRoute } from "next";
import { STATE_CODES } from "@/lib/stateCodes";
import { SITE_URL } from "@/lib/site";
import { ABOUT_CHAPTERS } from "@/lib/aboutPages";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// STATE_CODES comes from lib/stateCodes.ts, NOT from RaceMap: importing it
// from that "use client" module gave this server module a client reference
// instead of the object, and the sitemap silently shipped with no state
// URLs at all. See that file's comment.

// Rendered per request (with the backend fetch below cached for an hour),
// never prerendered: `next build` runs where the backend isn't reachable,
// so a static sitemap would bake in the no-backend fallback and ship
// without a single member, bill, or issue page until the next deploy.
export const dynamic = "force-dynamic";

interface SitemapEntry {
  id: string;
  lastmod: string | null;
}

interface SitemapIndex {
  politicians: SitemapEntry[];
  bills: SitemapEntry[];
  issues: SitemapEntry[];
  /** Days either chamber met. Optional only for a backend image older than
   * this field; the Explore documents are listed by /sitemap-index.xml. */
  congressDays?: string[];
}

async function fetchIndex(): Promise<SitemapIndex | null> {
  try {
    const res = await fetch(`${BACKEND}/api/sitemap`, { next: { revalidate: 3600 } });
    if (!res.ok) return null;
    const data = await res.json();
    if (
      !Array.isArray(data?.politicians) ||
      !Array.isArray(data?.bills) ||
      !Array.isArray(data?.issues)
    ) {
      return null;
    }
    return data as SitemapIndex;
  } catch {
    return null;
  }
}

function entries(
  items: SitemapEntry[],
  path: (id: string) => string,
  changeFrequency: MetadataRoute.Sitemap[number]["changeFrequency"],
  priority: number
): MetadataRoute.Sitemap {
  return items.map((item) => ({
    url: `${SITE_URL}${path(encodeURIComponent(item.id))}`,
    // Only a date the record actually carries. Stamping "now" on every URL
    // (what this file used to do for all of them) teaches Google the
    // site's lastmod values are meaningless, and it stops reading them.
    ...(item.lastmod ? { lastModified: item.lastmod } : {}),
    changeFrequency,
    priority,
  }));
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const index = await fetchIndex();

  const staticRoutes: MetadataRoute.Sitemap = [
    { url: `${SITE_URL}/`, changeFrequency: "daily", priority: 1 },
    { url: `${SITE_URL}/action`, changeFrequency: "hourly", priority: 0.9 },
    { url: `${SITE_URL}/politicians`, changeFrequency: "daily", priority: 0.9 },
    { url: `${SITE_URL}/elections`, changeFrequency: "daily", priority: 0.9 },
    { url: `${SITE_URL}/congress`, changeFrequency: "hourly", priority: 0.8 },
    { url: `${SITE_URL}/congress/bills`, changeFrequency: "hourly", priority: 0.8 },
    { url: `${SITE_URL}/leaderboard`, changeFrequency: "daily", priority: 0.8 },
    { url: `${SITE_URL}/compare`, changeFrequency: "weekly", priority: 0.6 },
    { url: `${SITE_URL}/explore`, changeFrequency: "daily", priority: 0.6 },
    { url: `${SITE_URL}/about`, changeFrequency: "monthly", priority: 0.5 },
    ...ABOUT_CHAPTERS.map((c) => ({
      url: `${SITE_URL}${c.href}`,
      changeFrequency: "monthly" as const,
      priority: 0.4,
    })),
    { url: `${SITE_URL}/changelog`, changeFrequency: "weekly", priority: 0.3 },
    { url: `${SITE_URL}/accessibility`, changeFrequency: "monthly", priority: 0.3 },
    { url: `${SITE_URL}/environmental`, changeFrequency: "monthly", priority: 0.3 },
    { url: `${SITE_URL}/feedback`, changeFrequency: "monthly", priority: 0.2 },
  ];

  const states: MetadataRoute.Sitemap = STATE_CODES.map((code) => ({
    url: `${SITE_URL}/elections/states/${code}`,
    changeFrequency: "daily" as const,
    priority: 0.8,
  }));

  if (!index) return [...staticRoutes, ...states];

  return [
    ...staticRoutes,
    ...states,
    ...entries(index.politicians, (id) => `/politicians/${id}`, "weekly", 0.8),
    ...entries(index.bills, (id) => `/congress/bills/${id}`, "weekly", 0.5),
    ...entries(index.issues, (id) => `/issue/${id}`, "weekly", 0.5),
    ...congressReports(index.congressDays ?? []),
  ];
}

/** The day, week and month reports for the days Congress met. A day's
 * page is that day's record; a week or month page covers the days in it,
 * so it changes as long as the latest of them does. */
function congressReports(days: string[]): MetadataRoute.Sitemap {
  const weeks = new Map<string, string>();
  const months = new Map<string, string>();
  for (const day of days) {
    const week = mondayOf(day);
    if (!weeks.has(week) || weeks.get(week)! < day) weeks.set(week, day);
    const month = day.slice(0, 7);
    if (!months.has(month) || months.get(month)! < day) months.set(month, day);
  }
  const page = (path: string, lastmod: string) => ({
    url: `${SITE_URL}${path}`,
    lastModified: lastmod,
    changeFrequency: "weekly" as const,
    priority: 0.4,
  });
  return [
    ...days.map((d) => page(`/congress/${d}`, d)),
    ...[...weeks].map(([w, last]) => page(`/congress/week/${w}`, last)),
    ...[...months].map(([m, last]) => page(`/congress/month/${m}`, last)),
  ];
}

/** YYYY-MM-DD of the Monday starting the week that contains `day` — the
 * week page's own address (congress_service.week_bounds). */
function mondayOf(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}
