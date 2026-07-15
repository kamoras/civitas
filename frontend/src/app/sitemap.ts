import type { MetadataRoute } from "next";

const BASE = "https://civitas-research.org";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: BASE, lastModified: now, changeFrequency: "daily", priority: 1 },
    { url: `${BASE}/action`, lastModified: now, changeFrequency: "hourly", priority: 0.9 },
    { url: `${BASE}/politicians`, lastModified: now, changeFrequency: "daily", priority: 0.9 },
    { url: `${BASE}/bills`, lastModified: now, changeFrequency: "hourly", priority: 0.8 },
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
