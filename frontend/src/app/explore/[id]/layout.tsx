import type { Metadata } from "next";

/** The title this route's 404 shows: a route's notFound() discards its
 * page's metadata, and a 404's head comes from the layouts above it (as
 * the roll call and bill pages do). A found document replaces it. Nothing
 * else: a found page would inherit it. */
export const metadata: Metadata = { title: "Document not found" };

export default function ExploreDocumentLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
