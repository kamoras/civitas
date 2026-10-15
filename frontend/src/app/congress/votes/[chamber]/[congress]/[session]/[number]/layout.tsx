import type { Metadata } from "next";

/** The title this route's 404 shows: a route's notFound() discards its
 * page's metadata, and a 404's head comes from the layouts above it. A
 * found vote's page replaces it. Nothing else: a found page would inherit
 * it. */
export const metadata: Metadata = { title: "Vote not found" };

export default function RollCallLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
