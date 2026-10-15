import type { Metadata } from "next";
import RecordNotFound from "@/components/notFound/RecordNotFound";

// Any address no route answers (a mistyped path, /congress/committees/x —
// committees have no pages of their own). The 404 status stays, and Next
// adds noindex; the title said only the site's name.
export const metadata: Metadata = { title: "Page not found" };

/** Any address no route answers. Record pages that miss (a member, a bill,
 * a vote, an issue, a document) have their own not-found.tsx, with the
 * next step for that kind of record. */
export default function NotFound() {
  return <RecordNotFound />;
}
