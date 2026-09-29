import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import type { MonthReport, PeriodDay, PeriodReport } from "@/types/congress";
import {
  CHAMBER_NAME,
  billHref,
  dayHref,
  longDate,
  monthHref,
  monthLabel,
  shortDate,
  weekHref,
} from "@/lib/congress";
import { CongressTabs, PeriodNav } from "./CongressNav";
import { Empty, Figure, FigureGroup, Section, VoteRow } from "./ReportParts";

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
  if (!parts.length && c.minutesInSession !== null && c.minutesInSession < 60)
    parts.push(`${c.minutesInSession} min`);
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
                <span className="font-mono text-xs uppercase tracking-[0.1em] text-ink-lo">
                  {shortDate(d.date)}
                </span>
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
          className="flex gap-3 border-b border-white/[0.09] py-2 text-[15px]"
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
          <span className="text-ink">
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
}: {
  report: PeriodReport | MonthReport;
  kind: "week" | "month";
}) {
  const { senate, house } = report.totals;
  const title =
    kind === "week"
      ? `Week of ${longDate(report.start).replace(/^\w+, /, "")}`
      : monthLabel(report.start.slice(0, 7));
  const pending = senate.daysPending + house.daysPending;
  const previous =
    kind === "week"
      ? { href: weekHref(report.previous), label: `Week of ${shortDate(report.previous).slice(5)}` }
      : { href: monthHref(`${report.previous}-01`), label: monthLabel(report.previous) };
  const next =
    kind === "week"
      ? { href: weekHref(report.next), label: `Week of ${shortDate(report.next).slice(5)}` }
      : { href: monthHref(`${report.next}-01`), label: monthLabel(report.next) };
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <CongressTabs active="reports" />
          <PageMasthead className="mb-6" eyebrow="What happened in Congress" title={title}>
            <p>{report.sentence}</p>
            {pending > 0 && (
              <p className="mt-1 font-sans text-sm text-ink-min">
                {pending} session {pending === 1 ? "day is" : "days are"} still on the live floor
                log; its passed and confirmed measures are added when the Daily Digest is published.
              </p>
            )}
          </PageMasthead>
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
          <div className="mt-8 grid gap-8 md:grid-cols-2">
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
                    className="grid gap-1 border-b border-white/[0.09] py-2.5 sm:grid-cols-[10rem_minmax(0,1fr)]"
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

          <div className="mt-10 grid gap-12 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="flex min-w-0 flex-col gap-6">
              <Section level={2} title="Record votes" count={report.votes.length}>
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
                <Section level={2} title="Became law" count={report.becameLaw.length}>
                  <BillList items={report.becameLaw} />
                </Section>
              )}
              <Section
                level={2}
                title="Passed both chambers"
                count={report.passedBothChambers.length}
              >
                {report.passedBothChambers.length ? (
                  <BillList items={report.passedBothChambers} />
                ) : (
                  <Empty>None.</Empty>
                )}
              </Section>
              <Section level={2} title="Passed one chamber" count={report.passedOneChamber.length}>
                {report.passedOneChamber.length ? (
                  <BillList items={report.passedOneChamber} />
                ) : (
                  <Empty>None.</Empty>
                )}
              </Section>
              {report.closestVotes.length > 0 && (
                <Section level={2} title="Closest votes">
                  <ul className="flex flex-col">
                    {report.closestVotes.map((v) => (
                      <li
                        key={`${v.chamber}-${v.number}`}
                        className="border-b border-white/[0.09] py-2 text-[15px] text-ink"
                      >
                        <span className="font-mono text-ink-lo">
                          {CHAMBER_NAME[v.chamber]} No. {v.number}
                        </span>{" "}
                        {v.result}, {v.yeas}–{v.nays}
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
