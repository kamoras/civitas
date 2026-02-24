import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "SCORECARD // CIVITAS",
  description:
    "See who funds your representatives, how they vote, and where the lobbying money goes. All public data.",
};

export default function ScorecardLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
