import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import type { ChamberDay, CongressEvent, DayReport } from "@/types/congress";
import { CHAMBER_NAME, dayHref, longDate, monthHref, shortDate, weekHref } from "@/lib/congress";
import { CongressTabs, PeriodNav } from "./CongressNav";
import { Empty, EventList, Figure, FigureGroup, MeasureRow, Section, VoteRow } from "./ReportParts";

const STATUS_LINE: Record<ChamberDay["status"], string> = {
  final: "Final record",
  live: "Live floor log",
  not_in_session: "Not in session",
  no_record: "Nothing recorded yet",
  no_record_published: "No Record for this day",
};

function passedTag(e: CongressEvent): string | null {
  if (e.nextStep === "both") return "Passed both chambers";
  if (e.nextStep) return `Goes to the ${CHAMBER_NAME[e.nextStep]}`;
  return null;
}

function ChamberHead({ day }: { day: ChamberDay }) {
  const live = day.status === "live";
  const times = day.convenedAt && day.adjournedAt
    ? `${day.convenedAt}–${day.adjournedAt}`
    : day.convenedAt
      ? `from ${day.convenedAt}`
      : null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-display text-3xl font-extrabold text-ink-hi">{CHAMBER_NAME[day.chamber]}</h2>
        <span
          className={`border px-2 py-1 font-mono text-xs uppercase tracking-[0.12em] ${
            live ? "border-phos-mid text-phos-mid" : "border-white/20 text-ink-lo"
          }`}
        >
          {STATUS_LINE[day.status]}
          {times && day.status !== "not_in_session" ? ` · ${times}` : ""}
        </span>
      </div>
      {day.status === "not_in_session" && day.adjournmentText && (
        <p className="text-sm leading-relaxed text-ink-lo">{day.adjournmentText}</p>
      )}
      {day.nextMeeting && (
        <p className="text-sm leading-relaxed text-ink-lo">
          Next meeting: {day.nextMeeting}.{day.nextProgram ? ` ${day.nextProgram}` : ""}
        </p>
      )}
      {day.floorLogStatus === "failed" && day.status !== "final" && (
        <p className="text-sm text-signal-amber">
          The {CHAMBER_NAME[day.chamber]}&apos;s floor log could not be read on the last check. What
          is shown may be incomplete.
        </p>
      )}
    </div>
  );
}

function ChamberColumn({ day }: { day: ChamberDay }) {
  const inSession = day.status === "final" || day.status === "live";
  const introduced =
    day.billsIntroduced !== null || day.resolutionsIntroduced !== null ? day.introducedText : null;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ChamberHead day={day} />
      {inSession && (
        <Section title="Record votes" count={day.votes.length}>
          {day.votes.length ? (
            <ul className="flex flex-col">
              {day.votes.map((v) => (
                <VoteRow key={v.number} vote={v} />
              ))}
            </ul>
          ) : (
            <Empty>No record votes.</Empty>
          )}
        </Section>
      )}
      {day.status === "live" && (
        <p className="border border-white/15 bg-surface px-3 py-2 text-sm text-ink-lo">
          Measures passed, reported and confirmed appear here when the Congressional Record&apos;s
          Daily Digest for this day is published, usually the next day. Until then this is the
          chamber&apos;s own floor log.
        </p>
      )}
      {day.passed.length > 0 && (
        <Section title="Passed" count={day.passed.length}>
          <EventList>
            {day.passed.map((e, i) => (
              <MeasureRow key={i} event={e} tag={passedTag(e)} />
            ))}
          </EventList>
        </Section>
      )}
      {day.failed.length > 0 && (
        <Section title="Failed" count={day.failed.length}>
          <EventList>
            {day.failed.map((e, i) => (
              <MeasureRow key={i} event={e} />
            ))}
          </EventList>
        </Section>
      )}
      {day.confirmed.length > 0 && (
        <Section title="Nominations confirmed" count={day.counts.confirmed}>
          <EventList>
            {day.confirmed.map((e, i) => (
              <MeasureRow key={i} event={e} />
            ))}
          </EventList>
        </Section>
      )}
      {day.reported.length > 0 && (
        <Section title="Reported from committee" count={day.reported.length}>
          <EventList>
            {day.reported.map((e, i) => (
              <MeasureRow key={i} event={e} />
            ))}
          </EventList>
        </Section>
      )}
      {introduced && (
        <Section title="Introduced">
          <p className="text-[15px] leading-relaxed text-ink">{introduced}</p>
        </Section>
      )}
      {day.status === "final" && (
        <Section title="Committee meetings" count={day.committees.length}>
          {day.committees.length ? (
            <ul className="flex flex-col">
              {day.committees.map((e, i) => (
                <li key={i} className="flex flex-col gap-1 border-b border-white/[0.09] py-2.5">
                  <span className="text-[15px] font-bold text-ink">{e.name}</span>
                  <span className="text-sm leading-relaxed text-ink-lo">{e.text}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No committee meetings.</Empty>
          )}
        </Section>
      )}
      {day.floorLog.length > 0 && (
        <Section title="Floor log" count={day.floorLog.length}>
          <details open={day.status === "live"} className="group">
            <summary className="cursor-pointer font-mono text-sm text-ink-lo hover:text-ink-hi">
              {day.status === "live" ? "Hide" : "Show"} the {CHAMBER_NAME[day.chamber]}&apos;s floor log
            </summary>
            <ol className="mt-2 flex flex-col">
              {day.floorLog.map((e, i) => (
                <li
                  key={i}
                  className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-3 border-b border-white/[0.09] py-2"
                >
                  <span className="font-mono text-xs tabular-nums text-ink-lo">{e.time ?? ""}</span>
                  <span className="text-sm leading-relaxed text-ink">
                    {e.name && <span className="font-bold">{e.name} </span>}
                    {e.text}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        </Section>
      )}
    </div>
  );
}

export default function DayReportView({ report }: { report: DayReport }) {
  const { senate, house } = report.chambers;
  const sources = (["senate", "house"] as const).map((c) => report.chambers[c]).filter((d) => d.sourceUrl);
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-6xl">
          <CongressTabs active="reports" />
          <PageMasthead className="mb-6" eyebrow="What happened in Congress" title={longDate(report.date)}>
            <p>{report.sentence}</p>
          </PageMasthead>
          <PeriodNav
            active="day"
            hrefs={{ day: dayHref(report.date), week: weekHref(report.date), month: monthHref(report.date) }}
            previous={report.previousDay ? { href: dayHref(report.previousDay), label: shortDate(report.previousDay) } : null}
            next={report.nextDay ? { href: dayHref(report.nextDay), label: shortDate(report.nextDay) } : null}
          />
          <div className="mt-8 grid gap-8 md:grid-cols-2">
            <FigureGroup name="Senate">
              <Figure value={senate.counts.recordVotes} label="Record votes" muted={!senate.counts.recordVotes} />
              <Figure value={senate.counts.billsPassed} label="Bills passed" muted={!senate.counts.billsPassed} />
              <Figure value={senate.counts.resolutionsPassed} label="Resolutions" muted={!senate.counts.resolutionsPassed} />
              <Figure value={senate.counts.confirmed} label="Confirmed" muted={!senate.counts.confirmed} />
            </FigureGroup>
            <FigureGroup name="House">
              <Figure value={house.counts.recordVotes} label="Record votes" muted={!house.counts.recordVotes} />
              <Figure value={house.counts.billsPassed} label="Bills passed" muted={!house.counts.billsPassed} />
              <Figure value={house.counts.resolutionsPassed} label="Resolutions" muted={!house.counts.resolutionsPassed} />
              <Figure value={house.counts.reported} label="Reported" muted={!house.counts.reported} />
            </FigureGroup>
          </div>
          <div className="mt-10 grid gap-12 md:grid-cols-2">
            <ChamberColumn day={senate} />
            <ChamberColumn day={house} />
          </div>
          <footer className="mt-12 border-t border-white/15 pt-5 text-sm leading-relaxed text-ink-lo">
            <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Source</p>
            <p className="mt-2 max-w-4xl">
              The Congressional Record&apos;s Daily Digest (Government Publishing Office) once
              published, and each chamber&apos;s own floor log and roll-call votes until then.
              Entries are the record&apos;s own wording.{" "}
              {sources.map((d, i) => (
                <span key={d.chamber}>
                  {i > 0 && " · "}
                  <Link href={d.sourceUrl as string} className="underline decoration-white/30 underline-offset-4 hover:text-phos">
                    {CHAMBER_NAME[d.chamber]} source
                  </Link>
                </span>
              ))}
            </p>
          </footer>
        </div>
      </main>
      <Footer />
    </div>
  );
}
