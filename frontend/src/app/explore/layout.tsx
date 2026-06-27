import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "EXPLORE // CIVITAS",
  description:
    "Search congressional bills, votes, and policy documents by topic. Find which members of Congress are shaping the issues that matter to you.",
  openGraph: {
    title: "Explore Congressional Bills & Votes — Civitas",
    description: "Search bills, votes, and policy documents by topic. Discover how Congress is acting on issues that affect you.",
    url: "https://civitas.paramain.com/explore",
  },
  twitter: {
    card: "summary_large_image",
    title: "Explore Congressional Bills & Votes — Civitas",
    description: "Search bills, votes, and policy documents by topic — all from public federal records.",
  },
};

export default function ExploreLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
