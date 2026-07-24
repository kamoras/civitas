import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "2026 Midterm Elections — Civitas",
  description:
    "Every 2026 Senate and House race — candidates, FEC fundraising totals, partisan lean, and live news coverage, all sourced from public federal data.",
  openGraph: {
    title: "2026 Midterm Elections — Civitas",
    description:
      "Track every 2026 Senate and House race: candidates, fundraising, partisan lean, and live coverage.",
    url: "https://civitas-research.org/elections",
  },
  twitter: {
    card: "summary_large_image",
    title: "2026 Midterm Elections — Civitas",
    description:
      "Track every 2026 Senate and House race: candidates, fundraising, and live coverage.",
  },
};

export default function ElectionsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
