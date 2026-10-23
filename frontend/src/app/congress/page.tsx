import Link from "next/link";
import { connection } from "next/server";
import { TOPIC_FEEDS, pageMetadata } from "@/lib/site";
import { fetchGlance, fetchLatestDay, fetchWeek } from "@/lib/congressServer";
import { RECORD_SOURCE, dayHref, longDate, monthHref, weekHref } from "@/lib/congress";
import { ordinal } from "@/components/scorecard/format";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import FollowFeed from "@/components/shared/FollowFeed";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import { SourceLine } from "@/components/shared/Type";
import { PeriodNav } from "@/components/congress/CongressNav";
import ThisWeekStrip from "@/components/shared/ThisWeekStrip";
import SectionTabs from "@/components/layout/SectionTabs";
import SectionCards from "@/components/sections/SectionCards";
import CongressGlance from "@/components/congress/CongressGlance";
import YourMembersRecord from "@/components/congress/YourMembersRecord";

export const metadata = pageMetadata({
  title: "This Congress: Days in Session, Bills and Votes",
  description:
    "The sitting Congress so far: the days each chamber met, how far its bills got, and every day's, week's and month's record of the Senate and the House, from the Congressional Record.",
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
  // In a recess the latest day is a pro forma sitting: the week and month
  // views open on the last week that had business (the backend decides,
  // report.recess), and a box says when each chamber sits next.
  const week = report?.recess ? await fetchWeek(report.recess.week) : null;
  const anchor = report ? (week?.start ?? report.date) : null;
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <SectionTabs section="congress" active="reports" />
          <PageMasthead
            className="mb-6"
            eyebrow="What happened on the floor"
            title={
              glance ? `The ${ordinal(glance.calendar.congress)} Congress so far` : "This Congress"
            }
          >
            {report ? (
              <p>
                The days each chamber met and how far this Congress&apos;s bills got. Day, Week and
                Month open the record of the latest sitting.
              </p>
            ) : (
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
            )}
            <FollowFeed className="mt-2" subject="Congress" feed={TOPIC_FEEDS.congress} />
          </PageMasthead>
          {report && anchor && (
            <PeriodNav
              active="congress"
              hrefs={{
                congress: "/congress",
                day: dayHref(report.date),
                week: weekHref(anchor),
                month: monthHref(anchor),
              }}
            />
          )}
          {report?.recess && week && (
            <BoxGrid columns={1} className="mt-6">
              <RecordBox
                level={2}
                kicker="Last week of business"
                title={report.recess.headline}
                href={weekHref(week.start)}
                dek={week.sentence}
                figure={report.recess.figure ?? null}
                more={{
                  label: "when Congress sits next",
                  children: (
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
              >
                <SourceLine>Source: {RECORD_SOURCE}.</SourceLine>
              </RecordBox>
            </BoxGrid>
          )}
          <div className="mt-8">
            {glance && <CongressGlance glance={glance} />}
            <YourMembersRecord />
            {report && <ThisWeekStrip variant="compact" pageDate={report.date} />}
          </div>
          <SectionCards section="congress" current="reports" className="mt-8" />
        </div>
      </main>
      <Footer />
    </div>
  );
}
