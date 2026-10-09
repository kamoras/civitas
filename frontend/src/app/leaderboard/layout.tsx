import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Congress Leaderboard: Senators & Representatives Ranked",
  description:
    "Every member of Congress ranked by representation score, campaign finance independence, and PAC funding, plus presidents ranked and Supreme Court justices listed, from public federal data.",
  path: "/leaderboard",
});

export default function LeaderboardLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
