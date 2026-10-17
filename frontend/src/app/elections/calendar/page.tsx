import { connection } from "next/server";
import type { ElectionCalendar, SpecialsBoard } from "@/types/specials";
import { TOPIC_FEEDS, pageMetadata } from "@/lib/site";
import { describeElectionCalendar } from "@/lib/seo";
import { CALENDAR_HREF } from "@/lib/routes";
import { safeHref } from "@/lib/formatting";
import SectionTabs from "@/components/layout/SectionTabs";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import FollowFeed from "@/components/shared/FollowFeed";
import SpecialsBoardView from "@/components/elections/specials/Specials";
import { longDate } from "@/lib/specials";
import CalendarList from "@/components/elections/specials/CalendarList";
import ClosingSoon from "@/components/elections/ClosingSoon";
import { INLINE_LINK } from "@/lib/controlStyles";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

async function read<T>(path: string): Promise<T> {
  const res = await fetch(`${BACKEND}${path}`, { next: { revalidate: 120 } });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export async function generateMetadata() {
  // Rendered per request (below), so this reads the backend, not a build.
  const board = await read<SpecialsBoard>("/api/elections/specials").catch(() => null);
  return pageMetadata({
    ...describeElectionCalendar(board?.upcoming.length ?? null, board?.vacancies.length ?? null),
    path: CALENDAR_HREF,
    feed: TOPIC_FEEDS.elections,
  });
}

export default async function ElectionCalendarPage() {
  // Never prerendered: `next build` runs where the backend isn't reachable.
  await connection();
  const [board, cal] = await Promise.all([
    read<SpecialsBoard>("/api/elections/specials"),
    read<ElectionCalendar>("/api/elections/calendar"),
  ]);
  const fec = safeHref(cal.sources.electionDates.url);
  const reports = safeHref(cal.sources.reportingDates.url);
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-4xl">
          <SectionTabs section="elections" active="calendar" />
          <PageMasthead eyebrow="Elections · calendar" title="Special elections and the calendar">
            <p>
              Seats in Congress empty today, the special elections that fill them, and every federal
              election date, voter deadline and campaign finance deadline in the year ahead.{" "}
              {cal.nextThirtyDays > 0
                ? `${cal.nextThirtyDays} fall in the next 30 days.`
                : cal.nextDate
                  ? `Nothing falls in the next 30 days; the next date is ${longDate(cal.nextDate)}.`
                  : "Nothing is on the calendar for the year ahead."}{" "}
              Dates are the FEC&apos;s and each state election office&apos;s, and each one&apos;s
              status is counted on its state&apos;s own clock.
            </p>
          </PageMasthead>
          <FollowFeed
            className="mt-2"
            subject="elections and new special elections"
            feed={TOPIC_FEEDS.elections}
          />

          {cal.registrationClosing && (
            <ClosingSoon
              rows={cal.registrationClosing}
              days={cal.closingSoonDays}
              className="mt-8 border border-phos/20 bg-surface px-4 py-3"
            />
          )}

          <section aria-labelledby="dates" className="mt-8">
            <h2
              id="dates"
              className="border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
            >
              Every date
            </h2>
            <div className="mt-4">
              <CalendarList cal={cal} />
            </div>
          </section>

          <section
            id="specials-board"
            aria-labelledby="specials"
            className="mt-10 border border-phos/20 bg-surface px-4 pb-4"
          >
            <h2
              id="specials"
              className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
            >
              Special elections
            </h2>
            <SpecialsBoardView board={board} />
          </section>

          <section className="mt-10 border-t border-white/[0.09] pt-4 text-xs leading-relaxed text-ink-min">
            <p>
              Election dates and special elections:{" "}
              {fec ? (
                <a href={fec} className={INLINE_LINK} rel="noopener">
                  the FEC&apos;s election calendar
                </a>
              ) : (
                "the FEC's election calendar"
              )}
              {cal.sources.electionDates.readOn &&
                `, read ${longDate(cal.sources.electionDates.readOn)}`}
              . Vacant seats: the House Clerk&apos;s and the Senate&apos;s member lists. Voter
              deadlines: each state&apos;s election office or its election law, for the{" "}
              {longDate(cal.voterDeadlinesFor)} election. Reporting deadlines:{" "}
              {reports ? (
                <a href={reports} className={INLINE_LINK} rel="noopener">
                  the FEC
                </a>
              ) : (
                "the FEC"
              )}
              , only those due on one date for every filer (a pre-primary report is due before each
              state&apos;s own primary, and the FEC&apos;s list doesn&apos;t say which state each
              date is for). A special&apos;s count is the state&apos;s own, read from its results
              feed after its polls close; Civitas calls no race.
            </p>
          </section>
        </div>
      </main>
      <Footer />
    </div>
  );
}
