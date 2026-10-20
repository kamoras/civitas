import { connection } from "next/server";
import { TITLE_TEMPLATE, pageMetadata } from "@/lib/site";

const TITLE = "Compare Members of Congress Side by Side";

export const metadata = {
  ...pageMetadata({
    title: TITLE,
    description:
      "Compare any two senators or representatives side by side: small gifts, PAC money, breaks with the party and bills moved, from public federal data.",
    path: "/compare",
  }),
  // A layout's plain-string title ends the root layout's template for the
  // routes below it: /compare/presidents was titled without " | Civitas
  // Research" (WCAG 2.4.2 asks for consistent, descriptive titles). Passing
  // the template on keeps every page under /compare on the site's pattern.
  title: { default: TITLE, template: TITLE_TEMPLATE },
};

// Rendered per request, so the page's query string (a linked pair) is known
// on the server and the page it renders is the one that stays: prerendered,
// the server sent a fallback that the page replaced after hydration, and
// that replacement repainted the masthead's line at 1.7 s on a throttled
// phone (2026-10-14) for no change a reader could see.
export default async function CompareLayout({ children }: { children: React.ReactNode }) {
  await connection();
  return <>{children}</>;
}
