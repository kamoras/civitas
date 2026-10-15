import { pageMetadata } from "@/lib/site";

// The index page only: this layout sits in the (index) route group, so a
// record page beneath (and its 404) never inherits this title or canonical.
// The page is a client component, so its metadata lives here; see lib/site.ts.
export const metadata = pageMetadata({
  title: "Bills in Congress: Status, Sponsors & What's Moving",
  description:
    "Track every bill moving through the current Congress (introduced, in committee, passed, or signed into law), with sponsors, latest actions, and related news.",
  path: "/congress/bills",
});

export default function BillsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
