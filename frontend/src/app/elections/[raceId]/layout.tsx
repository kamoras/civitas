import type { Metadata } from "next";

/** The title this route's 404 shows; a found archive page replaces it
 * (see notFoundMetadata in lib/site.ts). Nothing else: a found page would
 * inherit it. */
export const metadata: Metadata = { title: "No election or race at this address" };

export default function ElectionsSegmentLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
