import { pageMetadata } from "@/lib/site";

// Canonical is bare /action: ?tab= and ?issue= are views of this one page,
// not separate documents (the in-app ?tab=issues links are a router-cache
// workaround — see ACTION_CENTER_HREF — and must not index as duplicates).
export const metadata = pageMetadata({
  title: "Action Center: Today's Civic Issues & What You Can Do",
  description:
    "Nonpartisan summaries of today's top national issues, the bills and officials involved, ongoing national monitors, and concrete actions you can take.",
  path: "/action",
});

export default function ActionLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
