"use client";

import Link from "next/link";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import { SECTION_HEADING } from "@/components/action/IssueEnrichment";
import { CHAMBER_NAME, rollCallHref, voteTally } from "@/lib/congress";
import { formatUtcDate } from "@/lib/formatting";
import { monitorHref } from "@/lib/routes";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl } from "@/lib/site";
import type { IssueVote } from "@/types/action";

/**
 * A monitor's "On the record": the roll calls in Congress matched to its
 * updates (backend app/roll_call_links.py), as a strip of vote margins over
 * time, one row per chamber, with the same votes as a table.
 *
 * Each square is one vote: across, its date; up or down, yeas minus nays (the
 * backend's `margin`), so a square on the middle line is a tie and one far from
 * it was lopsided. Filled carried, hollow failed, as the chamber recorded
 * the result. The vote nearest a tie in each chamber is labelled. Nothing is
 * worked out here but where to draw: every figure is the record's.
 */

const CHAMBERS = ["senate", "house"] as const;

const shortDay = (d: string) => formatUtcDate(d, { month: "short", day: "numeric" });
const fullDay = (d: string) =>
  formatUtcDate(d, { month: "short", day: "numeric", year: "numeric" });

function marginText(v: IssueVote): string {
  const m = v.margin ?? 0;
  return m === 0 ? "Tie" : m > 0 ? `+${m}` : `−${-m}`;
}

function outcome(v: IssueVote): string {
  return v.rejected === true ? "Failed" : v.rejected === false ? "Carried" : v.result;
}

/** "Tied 212–212, May 14" or "49–50, Sep 24": the label on a chamber's
 *  closest vote. */
export function closestLabel(v: IssueVote): string {
  const tally = voteTally({ yeas: v.yeas ?? 0, nays: v.nays ?? 0 });
  return `${(v.margin ?? 0) === 0 ? `Tied ${tally}` : tally}, ${shortDay(v.date)}`;
}

/** The vote nearest a tie (the earliest among equals). */
export function closestVote(votes: IssueVote[]): IssueVote | undefined {
  return votes.reduce<IssueVote | undefined>(
    (best, v) =>
      best === undefined || Math.abs(v.margin ?? 0) < Math.abs(best.margin ?? 0) ? v : best,
    undefined
  );
}

// Where the plot ends, as a share of the row's width: the axis words sit
// beyond it, so no vote is drawn over them.
const PLOT_RIGHT = 84;

/** Where a vote sits in its row, as percentages of the row's box. */
export function position(v: IssueVote, first: string, last: string, maxAbs: number) {
  const span = Date.parse(last) - Date.parse(first);
  const t = span > 0 ? (Date.parse(v.date) - Date.parse(first)) / span : 0.5;
  return {
    left: 3 + t * (PLOT_RIGHT - 6),
    top: 50 - ((v.margin ?? 0) / Math.max(1, maxAbs)) * 40,
  };
}

const AXIS = "absolute right-0 font-mono text-[11px] text-ink-min";

function ChamberRow({
  chamber,
  votes,
  first,
  last,
  showDates,
}: {
  chamber: "senate" | "house";
  votes: IssueVote[];
  first: string;
  last: string;
  showDates: boolean;
}) {
  const maxAbs = Math.max(1, ...votes.map((v) => Math.abs(v.margin ?? 0)));
  const closest = closestVote(votes);
  const pos = closest ? position(closest, first, last, maxAbs) : null;
  const summary = `${CHAMBER_NAME[chamber]}: ${votes.length} vote${votes.length === 1 ? "" : "s"}${
    closest ? `, closest ${closestLabel(closest)}` : ""
  }.`;
  return (
    <div className="mt-3">
      <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
        {CHAMBER_NAME[chamber]} · {votes.length}
      </p>
      <div
        role="img"
        aria-label={summary}
        className="relative mt-1 h-28 border-y border-white/[0.07]"
      >
        <span
          aria-hidden="true"
          className="absolute left-0 top-1/2 border-t border-dashed border-ink-min/50"
          style={{ width: `${PLOT_RIGHT}%` }}
        />
        <span aria-hidden="true" className={`${AXIS} top-1`}>
          ↑ yeas
        </span>
        <span aria-hidden="true" className={`${AXIS} top-1/2 -translate-y-1/2`}>
          tie
        </span>
        <span aria-hidden="true" className={`${AXIS} bottom-1`}>
          ↓ nays
        </span>
        {votes.map((v) => {
          const p = position(v, first, last, maxAbs);
          return (
            <span
              key={`${v.chamber}-${v.congress}-${v.session}-${v.number}`}
              aria-hidden="true"
              className={`absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 border-2 border-phos ${
                v.rejected === true ? "bg-surface-base" : "bg-phos"
              }`}
              style={{ left: `${p.left}%`, top: `${p.top}%` }}
            />
          );
        })}
        {closest && pos && (
          <span
            aria-hidden="true"
            className={`absolute whitespace-nowrap font-mono text-xs text-ink-hi ${
              pos.left > PLOT_RIGHT / 2 ? "-translate-x-full pr-2" : "pl-2"
            } ${pos.top > 50 ? "-translate-y-full" : ""}`}
            style={{ left: `${pos.left}%`, top: `${pos.top}%` }}
          >
            {closestLabel(closest)}
          </span>
        )}
      </div>
      {showDates && (
        <p
          aria-hidden="true"
          className="mt-1 flex justify-between font-mono text-[11px] text-ink-min"
          style={{ width: `${PLOT_RIGHT}%` }}
        >
          <span>{shortDay(first)}</span>
          <span>{shortDay(last)}</span>
        </p>
      )}
    </div>
  );
}

export default function OnTheRecord({
  slug,
  title,
  votes,
}: {
  slug: string;
  title: string;
  votes: IssueVote[];
}) {
  if (votes.length === 0) return null;
  const first = votes[0].date;
  const last = votes[votes.length - 1].date;
  const sectionId = `on-the-record-${slug}`;
  return (
    <ShareSubjectProvider
      subject={{
        title,
        subtitle: "On the record: related votes in Congress",
        url: absoluteUrl(monitorHref(slug)),
        source: "Senate.gov and Clerk of the House roll-call vote records",
        asOf: last,
      }}
    >
      <section
        {...{ [SHARE_SECTION_ATTR]: sectionId }}
        id={sectionId}
        aria-labelledby={`${sectionId}-heading`}
        className="mt-4 max-w-3xl"
      >
        <div className={SECTION_HEADING}>
          <h4 id={`${sectionId}-heading`}>On the record</h4>
          <ShareSectionButton label="On the record" anchor={null} />
        </div>
        <p className="mt-2 font-display text-[15px] leading-relaxed text-ink-lo">
          {votes.length} related vote{votes.length === 1 ? "" : "s"} in Congress, {shortDay(first)}{" "}
          to {fullDay(last)}, matched to this concern&apos;s updates by the words of their record. A
          related vote is not necessarily the one a story reported. Each square is one vote: higher
          means more yeas than nays, the middle line a tie; filled carried, hollow failed.
        </p>
        {CHAMBERS.map((ch, i) => {
          const rows = votes.filter((v) => v.chamber === ch);
          const lastRow = !CHAMBERS.slice(i + 1).some((c) => votes.some((v) => v.chamber === c));
          return rows.length ? (
            <ChamberRow
              key={ch}
              chamber={ch}
              votes={rows}
              first={first}
              last={last}
              showDates={lastRow}
            />
          ) : null;
        })}
        <p className="mt-2 font-mono text-[11px] text-ink-min">
          Source: Senate.gov and the Clerk of the House roll-call records.
        </p>
        <details className="mt-2" data-share-exclude>
          <summary className="inline-flex min-h-[32px] cursor-pointer items-center font-mono text-xs tracking-[0.12em] text-phos-mid hover:text-phos">
            [ EVERY VOTE AS A TABLE ]
          </summary>
          <div className="overflow-x-auto">
            <table className="mt-2 w-full text-left text-[13px] text-ink">
              <caption className="sr-only">Related votes in Congress, oldest first</caption>
              <thead className="font-mono text-xs uppercase tracking-[0.08em] text-ink-min">
                <tr>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    Date
                  </th>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    Chamber
                  </th>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    Vote on
                  </th>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    Result
                  </th>
                  <th scope="col" className="py-1 font-normal">
                    Margin
                  </th>
                </tr>
              </thead>
              <tbody>
                {votes.map((v) => (
                  <tr
                    key={`${v.chamber}-${v.congress}-${v.session}-${v.number}`}
                    className="border-t border-white/[0.07] align-top"
                  >
                    <td className="whitespace-nowrap py-1.5 pr-3 font-mono text-xs">
                      {fullDay(v.date)}
                    </td>
                    <td className="py-1.5 pr-3">{CHAMBER_NAME[v.chamber]}</td>
                    <td className="min-w-[6.5rem] py-1.5 pr-3">
                      <Link
                        href={rollCallHref(v)}
                        className="underline decoration-white/20 underline-offset-4 hover:text-ink-hi"
                      >
                        {/* The bill's number, or the title of a vote with none (a
                          nomination); the full title is on the roll call's page. */}
                        <span className="line-clamp-2">{v.billLabel || v.title || v.question}</span>
                      </Link>
                    </td>
                    <td className="py-1.5 pr-3">
                      {outcome(v)} {voteTally({ yeas: v.yeas ?? 0, nays: v.nays ?? 0 })}
                    </td>
                    <td className="py-1.5 font-mono text-xs tabular-nums">{marginText(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </ShareSubjectProvider>
  );
}
