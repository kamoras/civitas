import { connection } from "next/server";
import type { CalendarBoxes, ElectionCalendar, SpecialsBoard } from "@/types/specials";
import type { RegistrationClosing } from "@/types/thisWeek";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import NoteLine from "@/components/elections/NoteLine";
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

/**
 * The page's boxes ("Boxes, not blocks"): the next deadline and the next
 * special election as countdowns, the states where registration closes
 * soon and the seats empty today as counts. Every line and figure is the
 * backend's (special_elections.calendar_boxes); the lists stay below, and
 * the states closing soon behind their box's "More".
 */
function CalendarBoxGrid({
  boxes,
  closing,
  days,
}: {
  boxes: CalendarBoxes;
  closing: RegistrationClosing[];
  days?: number;
}) {
  const { nextDeadline: next, registration, nextSpecial: special, vacancies } = boxes;
  return (
    <BoxGrid columns={2} className="mt-6">
      {next && (
        <RecordBox
          compact
          tone="deadline"
          kicker={`Next deadline · ${next.kicker}`}
          title={next.title}
          href={next.href ?? undefined}
          dek={next.dek}
          figure={
            next.closes && next.daysLeft !== null
              ? { type: "countdown", closes: next.closes, daysLeft: next.daysLeft, caption: "" }
              : null
          }
          links={[{ label: "Every date", href: "#dates" }]}
        />
      )}
      <RecordBox
        compact
        kicker="Voter registration"
        title="Registration closes soon"
        dek={registration.dek}
        figure={{
          type: "stat",
          value: registration.count,
          caption: `state${registration.count === 1 ? "" : "s"} in ${registration.days} days`,
        }}
        more={
          closing.length > 0
            ? {
                label: "each state and where to register",
                children: <ClosingSoon rows={closing} days={days} headingLevel={3} />,
              }
            : null
        }
      />
      <RecordBox
        compact
        tone={special.closes ? "deadline" : "neutral"}
        kicker="Next special election"
        title={special.title}
        href={special.href ?? undefined}
        dek={special.dek}
        figure={
          special.closes && special.daysLeft !== null
            ? { type: "countdown", closes: special.closes, daysLeft: special.daysLeft, caption: "" }
            : null
        }
        links={[{ label: "Special elections", href: "#specials-board" }]}
      />
      <RecordBox
        compact
        kicker="Vacancies"
        title="Seats empty today"
        dek={vacancies.dek}
        figure={{
          type: "stat",
          value: vacancies.count,
          caption: `seat${vacancies.count === 1 ? "" : "s"} empty`,
        }}
        links={vacancies.count > 0 ? [{ label: "Each seat", href: "#specials-board" }] : undefined}
      />
    </BoxGrid>
  );
}

export default async function ElectionCalendarPage() {
  // Never prerendered: `next build` runs where the backend isn't reachable.
  await connection();
  const [board, cal] = await Promise.all([
    read<SpecialsBoard>("/api/elections/specials"),
    read<ElectionCalendar>("/api/elections/calendar"),
  ]);
  const boxes = cal.boxes;
  const fec = safeHref(cal.sources.electionDates.url);
  const reports = safeHref(cal.sources.reportingDates.url);
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-4xl">
          <SectionTabs section="elections" active="calendar" />
          <PageMasthead
            eyebrow="Elections · calendar"
            title="Special elections and the calendar"
            more={
              <>
                {cal.nextThirtyDays > 0
                  ? `${cal.nextThirtyDays} fall in the next 30 days.`
                  : cal.nextDate
                    ? `Nothing falls in the next 30 days; the next date is ${longDate(cal.nextDate)}.`
                    : "Nothing is on the calendar for the year ahead."}{" "}
                Dates are the FEC&apos;s and each state election office&apos;s, and each one&apos;s
                status is counted on its state&apos;s own clock.
              </>
            }
          >
            <p>
              Seats in Congress empty today, the special elections that fill them, and every federal
              election date, voter deadline and campaign finance deadline in the year ahead.
            </p>
          </PageMasthead>
          <FollowFeed
            className="mt-2"
            subject="elections and new special elections"
            feed={TOPIC_FEEDS.elections}
          />

          {boxes ? (
            <CalendarBoxGrid
              boxes={boxes}
              closing={cal.registrationClosing ?? []}
              days={cal.closingSoonDays}
            />
          ) : (
            cal.registrationClosing && (
              <ClosingSoon
                rows={cal.registrationClosing}
                days={cal.closingSoonDays}
                className="mt-8 border border-phos/20 bg-surface px-4 py-3"
              />
            )
          )}

          <section aria-labelledby="dates" className="mt-8">
            <h2 id="dates" className="border-b border-white/15 pb-2 font-mono text-xs text-ink-min">
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
              className="border-b border-white/[0.07] py-2.5 font-mono text-xs text-ink-min"
            >
              Special elections
            </h2>
            <SpecialsBoardView board={board} />
          </section>

          <div className="mt-10 max-w-2xl">
            <NoteLine lead="Sources: the FEC, the House Clerk and the Senate, and each state's election office.">
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
            </NoteLine>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
