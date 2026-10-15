import { pageMetadata } from "@/lib/site";

// The index page only: this layout sits in the (index) route group, so a
// record page beneath (and its 404) never inherits this title or canonical.
// The page is a client component, so its metadata lives here; see lib/site.ts.
export const metadata = pageMetadata({
  title: "Directory of U.S. Senators, Representatives, President & Justices",
  description:
    "Find any U.S. senator, House representative, the president, or a Supreme Court justice by name, state, or party, and open their voting record, donors, and scorecard.",
  path: "/politicians",
});

export default function PoliticiansLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
