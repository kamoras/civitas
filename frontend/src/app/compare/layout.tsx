import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Compare Members of Congress Side by Side",
  description:
    "Compare any two senators or representatives side by side — voting records, campaign donors, PAC funding, and representation scores from public federal data.",
  path: "/compare",
});

export default function CompareLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
