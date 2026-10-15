import { connection } from "next/server";
import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Compare Members of Congress Side by Side",
  description:
    "Compare any two senators or representatives side by side: voting records, campaign donors, PAC funding, and representation scores from public federal data.",
  path: "/compare",
});

// Rendered per request, so the page's query string (a linked pair) is known
// on the server and the page it renders is the one that stays: prerendered,
// the server sent a fallback that the page replaced after hydration, and
// that replacement repainted the masthead's line at 1.7 s on a throttled
// phone (2026-10-14) for no change a reader could see.
export default async function CompareLayout({ children }: { children: React.ReactNode }) {
  await connection();
  return <>{children}</>;
}
