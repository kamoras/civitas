import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "LEADERBOARD // CIVITAS",
  description:
    "Rank all 535 members of Congress by representation score, PAC funding, and campaign finance independence — all sourced from public federal data.",
  openGraph: {
    title: "Congress Scorecard Leaderboard — Civitas",
    description: "See which senators and representatives score highest for independence from corporate PAC money. Ranked from public federal records.",
    url: "https://civitas.paramain.com/leaderboard",
  },
  twitter: {
    card: "summary_large_image",
    title: "Congress Leaderboard — Civitas",
    description: "Rank all 535 members of Congress by campaign finance independence and representation score.",
  },
};

export default function LeaderboardLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
