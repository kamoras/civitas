import { pageMetadata } from "@/lib/site";

// The index page only: this layout sits in the (index) route group, so a
// record page beneath (and its 404) never inherits this title or canonical.
// The page is a client component, so its metadata lives here; see lib/site.ts.
export const metadata = pageMetadata({
  title: "Search Congressional Records, Floor Speeches & Executive Orders",
  description:
    "Search floor speeches, executive orders, federal rules, and bills by topic, and see which members of Congress are shaping each issue: all from public federal records.",
  path: "/explore",
});

export default function ExploreLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
