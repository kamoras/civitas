import type { MetadataRoute } from "next";
import { FIPS_TO_STATE } from "@/components/elections/RaceMap";

const BASE = "https://civitas-research.org";

// The same 51 codes the map renders as clickable regions — one source, so
// a state the map can navigate to can never be one the sitemap omits.
const STATE_CODES = Array.from(new Set(Object.values(FIPS_TO_STATE))).sort();

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    ...STATE_CODES.map((code) => ({
      url: `${BASE}/elections/states/${code}`,
      lastModified: now,
      changeFrequency: "daily" as const,
      priority: 0.7,
    })),
    { url: BASE, lastModified: now, changeFrequency: "daily", priority: 1 },
    { url: `${BASE}/action`, lastModified: now, changeFrequency: "hourly", priority: 0.9 },
    { url: `${BASE}/politicians`, lastModified: now, changeFrequency: "daily", priority: 0.9 },
    { url: `${BASE}/bills`, lastModified: now, changeFrequency: "hourly", priority: 0.8 },
    { url: `${BASE}/elections`, lastModified: now, changeFrequency: "hourly", priority: 0.8 },
    { url: `${BASE}/leaderboard`, lastModified: now, changeFrequency: "daily", priority: 0.8 },
    { url: `${BASE}/compare`, lastModified: now, changeFrequency: "weekly", priority: 0.7 },
    { url: `${BASE}/explore`, lastModified: now, changeFrequency: "daily", priority: 0.7 },
    { url: `${BASE}/about`, lastModified: now, changeFrequency: "monthly", priority: 0.4 },
    { url: `${BASE}/changelog`, lastModified: now, changeFrequency: "weekly", priority: 0.3 },
    { url: `${BASE}/accessibility`, lastModified: now, changeFrequency: "monthly", priority: 0.3 },
    { url: `${BASE}/environmental`, lastModified: now, changeFrequency: "monthly", priority: 0.3 },
    { url: `${BASE}/feedback`, lastModified: now, changeFrequency: "monthly", priority: 0.3 },
  ];
}
