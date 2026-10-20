import type { Metadata } from "next";
import { pageMetadata } from "@/lib/site";
import { MEMBERS_HREF } from "@/lib/routes";
import { describeMembersDirectory } from "@/lib/seo";
import MembersDirectory from "./MembersDirectory";

// A client page's metadata, set by its server page (the directory reads the
// query string in the browser).
export const metadata: Metadata = pageMetadata({
  ...describeMembersDirectory(),
  path: MEMBERS_HREF,
});

export default function MembersPage() {
  return <MembersDirectory />;
}
