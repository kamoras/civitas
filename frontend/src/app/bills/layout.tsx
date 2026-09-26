import { pageMetadata } from "@/lib/site";

// /bills/[id] sets its own canonical; see lib/site.ts.
export const metadata = pageMetadata({
  title: "Bills in Congress: Status, Sponsors & What's Moving",
  description:
    "Track every bill moving through the current Congress — introduced, in committee, passed, or signed into law — with sponsors, latest actions, and related news.",
  path: "/bills",
});

export default function BillsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
