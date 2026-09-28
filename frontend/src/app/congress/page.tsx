import Link from "next/link";
import { connection } from "next/server";
import { pageMetadata } from "@/lib/site";
import { fetchLatestDay } from "@/lib/congressServer";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import DayReportView from "@/components/congress/DayReportView";
import { CongressTabs } from "@/components/congress/CongressNav";

export const metadata = pageMetadata({
  title: "What Happened in Congress Today",
  description:
    "Each day's record of the Senate and the House: bills passed, record votes with every member's vote, nominations confirmed and committee meetings, from the Congressional Record.",
  path: "/congress",
});

export default async function CongressPage() {
  // Rendered per request, never prerendered: `next build` runs where the
  // backend isn't reachable, so the prerendered page was always the empty
  // state below, served after every deploy until a reader's visit triggered
  // a revalidation. The fetch itself is still cached for five minutes.
  await connection();
  const report = await fetchLatestDay();
  if (report) return <DayReportView report={report} />;
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <CongressTabs active="reports" />
          <PageMasthead eyebrow="What happened in Congress" title="Congress">
            <p>
              No day of Congress has been recorded yet. The{" "}
              <Link href="/congress/bills" className="underline decoration-white/30 underline-offset-4 hover:text-phos">
                bills tab
              </Link>{" "}
              shows every bill moving through this Congress.
            </p>
          </PageMasthead>
        </div>
      </main>
      <Footer />
    </div>
  );
}
