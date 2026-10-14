import { connection } from "next/server";
import { pageMetadata } from "@/lib/site";
import { BillsInitialProvider, type BillsInitial } from "./BillsInitial";

// /congress/bills/[id] sets its own canonical; see lib/site.ts.
export const metadata = pageMetadata({
  title: "Bills in Congress: Status, Sponsors & What's Moving",
  description:
    "Track every bill moving through the current Congress (introduced, in committee, passed, or signed into law), with sponsors, latest actions, and related news.",
  path: "/congress/bills",
});

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The overview /congress/bills opens with (the page's own first request,
// lib/api fetchBillsInFlight), read per request: `next build` has no
// backend. Null when the backend can't answer; the page then reads it.
async function readOverview(): Promise<BillsInitial | null> {
  await connection();
  try {
    const res = await fetch(`${BACKEND}/api/bills?sort=recent&page=1&per_page=1`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<BillsInitial> | null;
    if (!body || typeof body.stageCounts !== "object" || body.stageCounts === null) return null;
    return { stageCounts: body.stageCounts, inMotion: body.inMotion ?? 0 };
  } catch {
    return null;
  }
}

export default async function BillsLayout({ children }: { children: React.ReactNode }) {
  return <BillsInitialProvider value={await readOverview()}>{children}</BillsInitialProvider>;
}
