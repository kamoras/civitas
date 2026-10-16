import Link from "next/link";
import { connection } from "next/server";
import { TOPIC_FEEDS, pageMetadata } from "@/lib/site";
import { fetchLatestDay, fetchWeek } from "@/lib/congressServer";
import { dayHref, longDate } from "@/lib/congress";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import FollowFeed from "@/components/shared/FollowFeed";
import DayReportView from "@/components/congress/DayReportView";
import PeriodReportView from "@/components/congress/PeriodReportView";
import ThisWeekStrip from "@/components/shared/ThisWeekStrip";
import SectionTabs from "@/components/layout/SectionTabs";

export const metadata = pageMetadata({
  title: "What Happened in Congress Today",
  description:
    "Each day's record of the Senate and the House: bills passed, record votes with every member's vote, nominations confirmed and committee meetings, from the Congressional Record.",
  path: "/congress",
  feed: TOPIC_FEEDS.congress,
});

export default async function CongressPage() {
  // Rendered per request, never prerendered: `next build` runs where the
  // backend isn't reachable, so the prerendered page was always the empty
  // state below, served after every deploy until a reader's visit triggered
  // a revalidation. The fetch itself is still cached for five minutes.
  await connection();
  const report = await fetchLatestDay();
  // A day neither chamber did business (a pro forma sitting) read "met for
  // 2 minutes" above eight zeros: lead with the recess and open the last
  // week that had business instead. The backend decides (report.recess).
  const week = report?.recess ? await fetchWeek(report.recess.week) : null;
  if (report?.recess && week) {
    return (
      <PeriodReportView
        report={week}
        kind="week"
        strip={<ThisWeekStrip variant="compact" pageDate={report.date} />}
        lead={{
          title: report.recess.headline,
          body: (
            <p>
              {report.recess.sentence}{" "}
              <Link
                href={dayHref(report.date)}
                className="underline decoration-white/30 underline-offset-4 hover:text-phos"
              >
                The latest sitting, {longDate(report.date)}
              </Link>
              .
            </p>
          ),
        }}
      />
    );
  }
  if (report)
    return (
      <DayReportView
        report={report}
        strip={<ThisWeekStrip variant="compact" pageDate={report.date} />}
      />
    );
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <SectionTabs section="congress" active="reports" />
          <PageMasthead eyebrow="What happened in Congress" title="Congress">
            <p>
              No day of Congress has been recorded yet. The{" "}
              <Link
                href="/congress/bills"
                className="underline decoration-white/30 underline-offset-4 hover:text-phos"
              >
                bills tab
              </Link>{" "}
              shows every bill moving through this Congress.
            </p>
            <FollowFeed className="mt-2" subject="Congress" feed={TOPIC_FEEDS.congress} />
          </PageMasthead>
        </div>
      </main>
      <Footer />
    </div>
  );
}
