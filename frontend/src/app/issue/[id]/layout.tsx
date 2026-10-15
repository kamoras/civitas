import type { Metadata } from "next";

/** The title this route's 404 shows; a found record's page replaces it
 * (see notFoundMetadata in lib/site.ts). Nothing else: a found page would
 * inherit it. */
export const metadata: Metadata = { title: "Issue not found" };

export default function IssueLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
