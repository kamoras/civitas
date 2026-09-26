import { pageMetadata } from "@/lib/site";

// /politicians/[id] sets its own canonical; anything else added under this
// segment must too, or it inherits this one (see lib/site.ts).
export const metadata = pageMetadata({
  title: "Directory of U.S. Senators, Representatives, President & Justices",
  description:
    "Find any U.S. senator, House representative, the president, or a Supreme Court justice by name, state, or party, and open their voting record, donors, and scorecard.",
  path: "/politicians",
});

export default function PoliticiansLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
