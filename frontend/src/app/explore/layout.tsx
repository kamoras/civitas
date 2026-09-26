import { pageMetadata } from "@/lib/site";

// /explore/[id] sets its own canonical in its layout; see lib/site.ts.
export const metadata = pageMetadata({
  title: "Search Congressional Records, Floor Speeches & Executive Orders",
  description:
    "Search floor speeches, executive orders, federal rules, and bills by topic, and see which members of Congress are shaping each issue — all from public federal records.",
  path: "/explore",
});

export default function ExploreLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
