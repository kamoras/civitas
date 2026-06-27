import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "COMPARE // CIVITAS",
  description:
    "Compare any two members of Congress side-by-side — funding sources, voting records, representation scores, and donor-vote connections.",
  openGraph: {
    title: "Compare Congress Members — Civitas",
    description: "Side-by-side comparison of campaign finance, voting records, and representation scores for any two members of Congress.",
    url: "https://civitas.paramain.com/compare",
  },
  twitter: {
    card: "summary_large_image",
    title: "Compare Congress Members — Civitas",
    description: "Compare funding, voting records, and representation scores for any two members of Congress.",
  },
};

export default function CompareLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
