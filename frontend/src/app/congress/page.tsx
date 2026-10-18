import Link from "next/link";
import { connection } from "next/server";
import { TOPIC_FEEDS, pageMetadata } from "@/lib/site";
import { fetchGlance, fetchLatestDay, fetchWeek } from "@/lib/congressServer";
import { dayHref, longDate } from "@/lib/congress";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import FollowFeed from "@/components/shared/FollowFeed";
import DayReportView from "@/components/congress/DayReportView";
import PeriodReportView from "@/components/congress/PeriodReportView";
import ThisWeekStrip from "@/components/shared/ThisWeekStrip";
import SectionTabs from "@/components/layout/SectionTabs";
import SectionCards from "@/components/sections/SectionCards";
import CongressGlance from "@/components/congress/CongressGlance";
import YourMembersRecord from "@/components/congress/YourMembersRecord";

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
  const [report, glance] = await Promise.all([fetchLatestDay(), fetchGlance()]);
  // A day neither chamber did business (a pro forma sitting) read "met for
  // 2 minutes" above eight zeros: lead with the recess and open the last
  // week that had business instead. The backend decides (report.recess).
  const week = report?.recess ? await fetchWeek(report.recess.week) : null;
  // The Congress so far in two pictures (the days each chamber sat, how far
  // its bills got), then the reader's own members when a state is picked in
  // the header, ahead of the day's or the week's record.
  const lead = (pageDate: string) => (
    <>
      {glance && <CongressGlance glance={glance} />}
      <YourMembersRecord />
      <ThisWeekStrip variant="compact" pageDate={pageDate} />
    </>
  );
  if (report?.recess && week) {
    return (
      <PeriodReportView
        report={week}
        kind="week"
        strip={lead(report.date)}
        hub={<SectionCards section="congress" current="reports" className="mt-6 mb-8" />}
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
        strip={lead(report.date)}
        hub={<SectionCards section="congress" current="reports" className="mt-6 mb-8" />}
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
          <SectionCards section="congress" current="reports" />
        </div>
      </main>
      <Footer />
    </div>
  );
}
