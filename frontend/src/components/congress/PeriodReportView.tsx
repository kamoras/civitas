import type { ReactNode } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import FollowFeed from "@/components/shared/FollowFeed";
import { TOPIC_FEEDS } from "@/lib/site";
import type { MonthReport, PeriodDay, PeriodReport } from "@/types/congress";
import {
  CHAMBER_NAME,
  billHref,
  dayHref,
  longDate,
  monthHref,
  monthLabel,
  rollCallHref,
  shortDate,
  voteResult,
  weekHref,
} from "@/lib/congress";
import { PeriodNav } from "./CongressNav";
import SectionTabs from "@/components/layout/SectionTabs";
import { Empty, Figure, FigureGroup, Section, TallyText, VoteRow } from "./ReportParts";

function chamberCell(day: PeriodDay, chamber: "senate" | "house") {
  const c = day[chamber];
  if (day.noRecordPublished) return <span className="text-ink-min">No Record</span>;
  if (!c.recorded) return <span className="text-ink-min">—</span>;
  if (!c.inSession) return <span className="text-ink-min">Not in session</span>;
  const parts = [];
  if (c.counts.recordVotes)
    parts.push(`${c.counts.recordVotes} vote${c.counts.recordVotes === 1 ? "" : "s"}`);
  if (c.counts.billsPassed)
    parts.push(`${c.counts.billsPassed} bill${c.counts.billsPassed === 1 ? "" : "s"} passed`);
  // The day's committee meetings are listed on its own page; a week or month
  // said nothing of them.
  if (c.counts.committeeMeetings)
    parts.push(
      `${c.counts.committeeMeetings} committee meeting${c.counts.committeeMeetings === 1 ? "" : "s"}`
    );
  if (!parts.length && c.minutesInSession !== null && c.minutesInSession < 60)
    // A Senate pro forma session lasts seconds; "0 min" read as no session.
    parts.push(c.minutesInSession === 0 ? "<1 min" : `${c.minutesInSession} min`);
  return <span className="text-ink">{parts.length ? parts.join(" · ") : "Met"}</span>;
}

function DayStrip({ days }: { days: PeriodDay[] }) {
  return (
    <div className="overflow-x-auto">
      <ol className="grid min-w-[40rem] grid-cols-7 gap-2">
        {days.map((d) => {
          const met = d.senate.inSession || d.house.inSession;
          return (
            <li key={d.date}>
              <Link
                href={dayHref(d.date)}
                className={`flex h-full min-h-32 flex-col gap-2 border p-3 text-sm transition-colors hover:border-white/40 ${
                  met ? "border-white/20 bg-surface" : "border-white/[0.09]"
                }`}
              >
                <span className="font-mono text-xs text-ink-lo">{shortDate(d.date)}</span>
                <span className="flex flex-col">
                  <span className="text-xs text-ink-min">Senate</span>
                  {chamberCell(d, "senate")}
                </span>
                <span className="flex flex-col">
                  <span className="text-xs text-ink-min">House</span>
                  {chamberCell(d, "house")}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function BillList({
  items,
}: {
  items: {
    billId: string | null;
    billLabel: string | null;
    congress?: number | null;
    name: string;
    date: string;
    chamber?: string;
  }[];
}) {
  return (
    <ul className="flex flex-col">
      {items.map((e, i) => (
        <li
          key={`${e.billId}-${i}`}
          className="flex flex-wrap gap-x-3 border-b border-white/[0.09] py-2 text-[15px]"
        >
          {e.billId ? (
            <Link
              href={billHref(e.billId, e.congress)}
              className="min-w-24 shrink-0 font-mono text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
            >
              {e.billLabel ?? e.billId}
            </Link>
          ) : (
            <span className="min-w-24 shrink-0" />
          )}
          {/* Under the bill number where it can't have 10rem beside it (a
              phone at 200% text), not pushed past the screen. */}
          <span className="min-w-[min(10rem,100%)] flex-1 text-ink">
            {e.name}
            <span className="ml-2 font-mono text-xs text-ink-min">{shortDate(e.date)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function PeriodReportView({
  report,
  kind,
  lead,
  strip,
  hub,
}: {
  report: PeriodReport | MonthReport;
  kind: "week" | "month";
  /** /congress during a recess: the masthead says Congress is out and
   *  introduces this week as the last one with business. */
  lead?: { title: string; body: ReactNode };
  /** The "this week" strip, rendered by the server page. */
  strip?: ReactNode;
  /** The section hub's cards (/congress only), rendered by the server page. */
  hub?: ReactNode;
}) {
  const { senate, house } = report.totals;
  const title =
    kind === "week"
      ? `Week of ${longDate(report.start).replace(/^\w+, /, "")}`
      : monthLabel(report.start.slice(0, 7));
  const pending = senate.daysPending + house.daysPending;
  // In a recess the week is the last one with business, under the page's
  // own lead: its lists fold behind their headings (each says its count),
  // and the full week is a tab away.
  const folded = Boolean(lead);
  // A neighbour only when it has a record to show (the API says null past
  // either end of the record).
  const step = (period: string | null) =>
    period == null
      ? null
      : kind === "week"
        ? { href: weekHref(period), label: `Week of ${shortDate(period).slice(5)}` }
        : { href: monthHref(`${period}-01`), label: monthLabel(period) };
  const previous = step(report.previous);
  const next = step(report.next);
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          {/* /congress in a recess is still the Today tab, showing a week. */}
          <SectionTabs section="congress" active={lead ? "reports" : kind} />
          <PageMasthead
            className="mb-6"
            eyebrow="What happened in Congress"
            title={lead?.title ?? title}
          >
            {lead ? (
              <>
                {lead.body}
                <h2 className="mt-4 font-mono text-xs text-ink-min">{title}</h2>
                <p className="mt-1">{report.sentence}</p>
              </>
            ) : (
              <p>{report.sentence}</p>
            )}
            {pending > 0 && (
              <p className="mt-1 font-sans text-sm text-ink-min">
                {pending} session {pending === 1 ? "day is" : "days are"} still on the live floor
                log; its passed and confirmed measures are added when the Daily Digest is published.
              </p>
            )}
            <FollowFeed className="mt-2" subject="Congress" feed={TOPIC_FEEDS.congress} />
          </PageMasthead>
          {strip}
          {hub}
          <PeriodNav
            active={kind}
            hrefs={{
              day: dayHref(report.start),
              week: weekHref(report.start),
              month: monthHref(report.start),
            }}
            previous={previous}
            next={next}
          />
          <div className="mt-8 grid grid-cols-1 gap-8 md:grid-cols-2">
            <FigureGroup name="Senate">
              <Figure
                value={senate.daysInSession}
                label="Days in session"
                muted={!senate.daysInSession}
              />
              <Figure value={senate.recordVotes} label="Record votes" muted={!senate.recordVotes} />
              <Figure value={senate.billsPassed} label="Bills passed" muted={!senate.billsPassed} />
              <Figure value={senate.confirmed} label="Confirmed" muted={!senate.confirmed} />
            </FigureGroup>
            <FigureGroup name="House">
              <Figure
                value={house.daysInSession}
                label="Days in session"
                muted={!house.daysInSession}
              />
              <Figure value={house.recordVotes} label="Record votes" muted={!house.recordVotes} />
              <Figure value={house.billsPassed} label="Bills passed" muted={!house.billsPassed} />
              <Figure
                value={house.billsIntroduced}
                label="Bills introduced"
                muted={!house.billsIntroduced}
              />
            </FigureGroup>
          </div>

          {kind === "week" ? (
            <div className="mt-10">
              <DayStrip days={report.days} />
            </div>
          ) : (
            <Section level={2} title="Weeks">
              <ul className="flex flex-col">
                {(report as MonthReport).weeks.map((w) => (
                  <li
                    key={w.week}
                    className="grid grid-cols-1 gap-1 border-b border-white/[0.09] py-2.5 sm:grid-cols-[10rem_minmax(0,1fr)]"
                  >
                    <Link
                      href={weekHref(w.week)}
                      className="font-mono text-sm text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
                    >
                      {shortDate(w.start).slice(5)} – {shortDate(w.end).slice(5)}
                    </Link>
                    <span className="text-[15px] text-ink">{w.sentence}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <div className="mt-10 grid grid-cols-1 gap-12 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="flex min-w-0 flex-col gap-6">
              <Section level={2} title="Record votes" count={report.votes.length} folded={folded}>
                {report.votes.length ? (
                  <ul className="flex flex-col">
                    {report.votes.map((v) => (
                      <VoteRow
                        key={`${v.chamber}-${v.number}`}
                        vote={{ ...v, question: `${CHAMBER_NAME[v.chamber]} · ${v.question}` }}
                        showDate
                      />
                    ))}
                  </ul>
                ) : (
                  <Empty>No record votes.</Empty>
                )}
              </Section>
            </div>
            <div className="flex min-w-0 flex-col gap-6">
              {report.becameLaw.length > 0 && (
                <Section
                  level={2}
                  title="Became law"
                  count={report.becameLaw.length}
                  folded={folded}
                >
                  <BillList items={report.becameLaw} />
                </Section>
              )}
              <Section
                level={2}
                title="Passed both chambers"
                count={report.passedBothChambers.length}
                folded={folded}
              >
                {report.passedBothChambers.length ? (
                  <BillList items={report.passedBothChambers} />
                ) : (
                  <Empty>None.</Empty>
                )}
              </Section>
              <Section
                level={2}
                title="Passed one chamber"
                count={report.passedOneChamber.length}
                folded={folded}
              >
                {report.passedOneChamber.length ? (
                  <BillList items={report.passedOneChamber} />
                ) : (
                  <Empty>None.</Empty>
                )}
              </Section>
              {report.closestVotes.length > 0 && (
                <Section level={2} title="Closest votes" folded={folded}>
                  <ul className="flex flex-col">
                    {report.closestVotes.map((v) => (
                      <li
                        key={`${v.chamber}-${v.number}`}
                        className="border-b border-white/[0.09] py-2 text-[15px] text-ink"
                      >
                        <Link
                          href={rollCallHref(v)}
                          className="font-mono text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
                        >
                          {CHAMBER_NAME[v.chamber]} No. {v.number}
                        </Link>{" "}
                        {voteResult(v)}, <TallyText vote={v} />
                        {v.billLabel && v.billId && (
                          <>
                            {" · "}
                            <Link
                              href={billHref(v.billId, v.congress)}
                              className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                            >
                              {v.billLabel}
                            </Link>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
            </div>
          </div>
        </div>
      </main>
      <Footer />
    </div>
  );
}
