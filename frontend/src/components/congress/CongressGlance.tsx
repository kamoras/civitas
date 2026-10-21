import Link from "next/link";
import type { CongressGlance as Glance } from "@/types/congress";
import { monthHref, monthLabel, reachedHref, shortDate } from "@/lib/congress";
import { shareMetaAttrs, SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import { ordinal } from "@/components/scorecard/format";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import ScrollRegion from "@/components/shared/ScrollRegion";

const CAL_SOURCE = "Congressional Record (Daily Digest); Senate and House roll calls";
const PIPE_SOURCE = "Congress.gov bill status, for every bill sitting members sponsored";

// The calendar's grid, in SVG units: a month per row, a day per column,
// each cell split into the Senate (top) and the House (bottom).
const LABEL_W = 34;
const CELL = 10;
const HALF = 4.25;
const ROW = 12;

const n = (x: number) => x.toLocaleString("en-US");

// Each chamber its own hue, so the two halves of a day read apart.
const FILL = { senate: "fill-phos", house: "fill-signal-cyan" } as const;
const SWATCH = { senate: "bg-phos", house: "bg-signal-cyan" } as const;

function Head({ id, title, share }: { id: string; title: string; share: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-white/15 pb-2">
      <h2 id={id} className="font-mono text-xs text-ink-min">
        {title}
      </h2>
      <ShareSectionButton label={share} />
    </div>
  );
}

/** A sitting's shade: brighter the more record votes the chamber took, so
 * a day of debate and a day of votes read apart; a pro forma or debate day
 * (no votes) is the dimmest lit cell. */
function shade(votes: number, max: number) {
  return max > 0 ? 0.35 + 0.65 * (votes / max) : 1;
}

function SittingCalendar({ cal }: { cal: Glance["calendar"] }) {
  const months = cal.months;
  const height = months.length * ROW;
  const width = LABEL_W + 31 * CELL;
  const { senate, house } = cal.totals;
  const summary = `Days each chamber sat in the ${ordinal(cal.congress)} Congress, ${shortDate(cal.start)} to ${shortDate(cal.today)}: the Senate ${n(senate.days)} days with ${n(senate.votes)} record votes, the House ${n(house.days)} days with ${n(house.votes)} record votes.`;
  return (
    <section
      aria-labelledby="calendar-heading"
      {...{ [SHARE_SECTION_ATTR]: "sitting-calendar" }}
      {...shareMetaAttrs({ source: CAL_SOURCE, asOf: cal.today })}
      className="min-w-0"
    >
      <Head id="calendar-heading" title="Days in session this Congress" share="Days in session" />
      <p className="mt-3 text-sm text-ink">
        The Senate sat <strong className="text-ink-hi">{n(senate.days)}</strong> days and the House{" "}
        <strong className="text-ink-hi">{n(house.days)}</strong> since {shortDate(cal.start)}.
      </p>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby="calendar-title"
        className="mt-3 h-auto w-full max-w-xl"
      >
        <title id="calendar-title">{summary}</title>
        {months.map((m, row) => {
          const [y, mo] = m.month.split("-").map(Number);
          const daysIn = new Date(Date.UTC(y, mo, 0)).getUTCDate();
          const top = row * ROW;
          return (
            <g key={m.month}>
              <text
                x={0}
                y={top + 9}
                className="fill-ink-min font-mono"
                fontSize={7.5}
                aria-hidden="true"
              >
                {monthLabel(m.month).slice(0, 3)} {String(y).slice(2)}
              </text>
              {Array.from({ length: daysIn }, (_, i) => {
                const iso = `${m.month}-${String(i + 1).padStart(2, "0")}`;
                if (iso < cal.start || iso > cal.today) return null;
                const day = cal.days[iso] ?? {};
                const held = !cal.recordFrom || iso >= cal.recordFrom;
                const x = LABEL_W + i * CELL;
                return (
                  <g key={iso}>
                    {(["senate", "house"] as const).map((c, k) => {
                      const v = day[c];
                      return (
                        <rect
                          key={c}
                          x={x}
                          y={top + 1 + k * (HALF + 1)}
                          width={CELL - 1.5}
                          height={HALF}
                          className={v === undefined ? "fill-white" : FILL[c]}
                          fillOpacity={
                            v === undefined ? (held ? 0.07 : 0.02) : shade(v, cal.maxVotes)
                          }
                        />
                      );
                    })}
                    {iso === cal.today && (
                      <rect
                        x={x - 0.75}
                        y={top + 0.25}
                        width={CELL}
                        height={ROW - 1.5}
                        fill="none"
                        className="stroke-signal-cyan"
                        strokeWidth={1}
                      />
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo">
        {(["senate", "house"] as const).map((c) => (
          <span key={c} className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className={`inline-block h-2 w-3 ${SWATCH[c]}`} />
            {c === "senate" ? "Senate (top half)" : "House (bottom half)"}
          </span>
        ))}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-ink-min">
        One row a month, one cell a day. Lit when the chamber sat, brighter the more record votes it
        took. Today is outlined.
      </p>
      <details className="mt-2 text-sm">
        <summary className="min-h-6 cursor-pointer text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos">
          The calendar as a table
        </summary>
        <ScrollRegion label="The calendar as a table" className="mt-2">
          <table className="w-full text-left font-mono text-xs tabular-nums">
            <caption className="sr-only">Days in session and record votes by month</caption>
            <thead className="text-ink-min">
              <tr>
                <th scope="col" className="py-1 pr-3 font-normal">
                  Month
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  Senate days
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  Senate votes
                </th>
                <th scope="col" className="py-1 pr-3 text-right font-normal">
                  House days
                </th>
                <th scope="col" className="py-1 text-right font-normal">
                  House votes
                </th>
              </tr>
            </thead>
            <tbody className="text-ink">
              {months.map((m) => (
                <tr key={m.month} className="border-t border-white/[0.07]">
                  <th scope="row" className="py-1 pr-3 font-normal">
                    <Link
                      href={monthHref(`${m.month}-01`)}
                      className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                    >
                      {monthLabel(m.month)}
                    </Link>
                  </th>
                  <td className="py-1 pr-3 text-right">{m.senate.days}</td>
                  <td className="py-1 pr-3 text-right">{m.senate.votes}</td>
                  <td className="py-1 pr-3 text-right">{m.house.days}</td>
                  <td className="py-1 text-right">{m.house.votes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollRegion>
      </details>
    </section>
  );
}

function BillPipeline({ pipe, asOf }: { pipe: NonNullable<Glance["pipeline"]>; asOf: string }) {
  const top = Math.log10(Math.max(...pipe.milestones.map((m) => m.bills)) + 1) || 1;
  const law = pipe.milestones[pipe.milestones.length - 1];
  return (
    <section
      aria-labelledby="pipeline-heading"
      {...{ [SHARE_SECTION_ATTR]: "bill-pipeline" }}
      {...shareMetaAttrs({ source: PIPE_SOURCE, asOf })}
      className="min-w-0"
    >
      <Head id="pipeline-heading" title="How far this Congress's bills got" share="Bill pipeline" />
      <p className="mt-3 text-sm text-ink">
        Of <strong className="text-ink-hi">{n(pipe.total)}</strong> bills sitting members
        introduced, <strong className="text-ink-hi">{n(law.bills)}</strong> became law (
        {law.shareText}).
      </p>
      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">
          Bills that reached each step this Congress, drawn on a log scale
        </caption>
        <thead className="sr-only">
          <tr>
            <th scope="col">Step</th>
            <th scope="col">Bills</th>
            <th scope="col">Share</th>
          </tr>
        </thead>
        <tbody>
          {pipe.milestones.map((m) => (
            <tr key={m.label} className="border-t border-white/[0.07] align-middle">
              <th
                scope="row"
                className="w-[42%] py-1 pr-3 text-left font-normal text-ink [overflow-wrap:anywhere]"
              >
                {/* The label and its bar are one link to the very bills the
                    row counts (GET /api/bills?reached=, the same rule as
                    these counts), so the target is the row's height. */}
                <Link
                  href={reachedHref(m.stage)}
                  aria-label={`${m.label}: ${n(m.bills)} ${m.bills === 1 ? "bill" : "bills"}`}
                  className="group block min-h-6 py-1 underline decoration-white/30 underline-offset-4 hover:text-phos"
                >
                  {m.label}
                  <span
                    aria-hidden="true"
                    className="mt-1 block h-2.5 bg-white/[0.07]"
                    style={{ width: "100%" }}
                  >
                    <span
                      className="block h-full bg-signal-cyan group-hover:bg-phos"
                      style={{ width: `${(Math.log10(m.bills + 1) / top) * 100}%` }}
                    />
                  </span>
                </Link>
              </th>
              <td className="py-2 pr-2 text-right font-mono text-base tabular-nums text-ink-hi">
                {n(m.bills)}
              </td>
              <td className="py-2 text-right font-mono text-xs tabular-nums text-ink-lo">
                {m.shareText}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs leading-relaxed text-ink-min">
        Bars on a log scale, so the few bills near the end still show. Each share is of every bill
        introduced. Pick a step to list its bills.{" "}
        <Link
          href="/congress/bills"
          className="underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          Every bill in motion
        </Link>
      </p>
    </section>
  );
}

/**
 * /congress's lead pictures: the days each chamber sat this Congress and
 * how far its bills got, both from GET /api/congress/glance and both
 * shareable, each with its table. The figures are the backend's; this
 * only lays them out (the log scale is a drawing choice).
 */
export default function CongressGlance({ glance }: { glance: Glance }) {
  const { calendar, pipeline } = glance;
  return (
    <ShareSubjectProvider
      subject={{
        title: `The ${ordinal(calendar.congress)} Congress`,
        subtitle: "At a glance, on Civitas",
        url: absoluteUrl("/congress"),
        source: CAL_SOURCE,
        asOf: calendar.today,
      }}
    >
      <div className="mb-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
        <SittingCalendar cal={calendar} />
        {pipeline && pipeline.total > 0 && <BillPipeline pipe={pipeline} asOf={calendar.today} />}
      </div>
    </ShareSubjectProvider>
  );
}
