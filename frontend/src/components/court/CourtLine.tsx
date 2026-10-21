import Link from "next/link";
import type { Court } from "@/types/court";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import { axisAt, positionDomain, signedPosition, surname } from "@/lib/court";
import { onThisPage } from "@/lib/onThisPage";
import StripPoints from "@/components/shared/Strip";
import { More } from "@/components/shared/RecordBox";

const MQ_SOURCE = "Martin-Quinn scores (Martin and Quinn 2002), mqscores.wustl.edu";

/**
 * The sitting justices on one left-right line by Martin-Quinn position,
 * then the same line justice by justice with each estimate's 95% credible
 * interval. The rows are a table: its numbers are the chart's text
 * equivalent, and each name opens the justice's page.
 */
export default function CourtLine({ court }: { court: Court }) {
  const placed = court.justices.filter((j) => j.mq);
  const unplaced = court.justices.filter((j) => !j.mq);
  const [lo, hi] = positionDomain(placed);
  const median = new Set(court.median);
  const at = (x: number) => `${axisAt(x, lo, hi)}%`;
  const zero = at(0);

  return (
    <section
      id="line"
      {...onThisPage("Where the justices sit")}
      aria-labelledby="line-heading"
      {...{ [SHARE_SECTION_ATTR]: "line" }}
      {...shareMetaAttrs({ source: MQ_SOURCE, asOf: null })}
      className="flex flex-col gap-4 border border-white/25 bg-surface px-4 py-5 sm:px-6"
    >
      <div>
        <h2 id="line-heading" className="text-xl font-semibold text-ink-hi">
          Where the justices sit
        </h2>
        <p className="mt-1 font-mono text-xs text-ink-min">
          Martin-Quinn estimate{court.mqTerm ? `, ${court.mqTerm} term` : ""} · not a score
        </p>
      </div>

      {placed.length === 0 ? (
        <p className="text-base text-ink">
          No Martin-Quinn position is stored yet. Martin and Quinn publish each term&apos;s
          estimates after it ends.
        </p>
      ) : (
        <>
          <p className="max-w-prose text-base text-ink">
            Left is more liberal, right more conservative; the median justice is marked.
          </p>

          {/* The one line: every justice's estimate as a tick, the median
              marked. Drawn only; the table below states every figure. */}
          <div className="px-2 pt-2">
            <StripPoints
              label="Martin-Quinn positions, sitting justices"
              points={placed.map((j) => ({
                id: j.id,
                name: j.name,
                shown: signedPosition(j.mq!.position),
                rank: j.rank ?? "",
                href: `/politicians/${encodeURIComponent(j.id)}`,
                x: axisAt(j.mq!.position, lo, hi),
                y: 22,
              }))}
              className="h-10"
            >
              <span className="absolute inset-x-0 top-5 h-px bg-white/30" />
              <span className="absolute top-2 h-7 w-px bg-white/40" style={{ left: zero }} />
              {placed.map((j) => (
                <span
                  key={j.id}
                  className={`absolute top-3 -ml-1.5 h-5 w-3 border ${
                    median.has(j.id)
                      ? "border-signal-amber bg-signal-amber"
                      : "border-ink-hi bg-ink-hi/80"
                  }`}
                  style={{ left: at(j.mq!.position) }}
                />
              ))}
            </StripPoints>
            <div aria-hidden="true" className="flex justify-between font-mono text-xs text-ink-min">
              <span>&larr; liberal</span>
              <span>conservative &rarr;</span>
            </div>
          </div>

          <div
            className="overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label="Justices by position"
          >
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                Sitting justices, left to right by Martin-Quinn position
                {court.mqTerm ? ` for the ${court.mqTerm} term` : ""}, with the 95% credible
                interval
              </caption>
              <thead>
                <tr className="border-b border-white/15 text-left font-mono text-xs text-ink-min">
                  <th scope="col" className="py-2 pr-3 font-normal">
                    Justice
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-normal">
                    Position (95% interval)
                  </th>
                  <th scope="col" className="hidden w-1/2 py-2 font-normal sm:table-cell">
                    <span className="sr-only">On the line</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {placed.map((j) => {
                  const mq = j.mq!;
                  const isMedian = median.has(j.id);
                  return (
                    <tr key={j.id} className="border-b border-white/[0.07]">
                      <th scope="row" className="py-2 pr-3 text-left font-normal">
                        <Link
                          href={`/politicians/${encodeURIComponent(j.id)}`}
                          className="inline-flex min-h-6 items-center text-ink-hi underline decoration-white/25 underline-offset-4 hover:text-phos"
                        >
                          {surname(j)}
                        </Link>
                        {isMedian && (
                          <span className="ml-2 border border-signal-amber/60 px-1 font-mono text-xs text-signal-amber">
                            Median
                          </span>
                        )}
                      </th>
                      <td className="py-2 pr-3 text-right font-mono tabular-nums text-ink">
                        {signedPosition(mq.position)}
                        {mq.low !== null && mq.high !== null && (
                          <span className="inline-block text-ink-lo">
                            &nbsp;({signedPosition(mq.low)} to {signedPosition(mq.high)})
                          </span>
                        )}
                      </td>
                      <td className="hidden py-2 sm:table-cell" aria-hidden="true">
                        <div className="relative h-4">
                          <span className="absolute inset-x-0 top-2 h-px bg-white/15" />
                          <span
                            className="absolute top-0 h-4 w-px bg-white/30"
                            style={{ left: zero }}
                          />
                          {mq.low !== null && mq.high !== null && (
                            <span
                              className="absolute top-[7px] h-0.5 bg-ink-lo"
                              style={{ left: at(mq.low), right: `calc(100% - ${at(mq.high)})` }}
                            />
                          )}
                          <span
                            className={`absolute top-0.5 -ml-1.5 h-3 w-3 ${isMedian ? "bg-signal-amber" : "bg-ink-hi"}`}
                            style={{ left: at(mq.position) }}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {unplaced.length > 0 && (
            <p className="text-sm text-ink-lo">
              No position for the {court.mqTerm} term yet:{" "}
              {unplaced.map((j, i) => (
                <span key={j.id}>
                  {i > 0 && ", "}
                  <Link
                    href={`/politicians/${encodeURIComponent(j.id)}`}
                    className="underline underline-offset-2 hover:text-phos"
                  >
                    {surname(j)}
                  </Link>
                </span>
              ))}
              . A justice is placed once they have voted through a term Martin and Quinn have
              published.
            </p>
          )}
          <More label="how the line is estimated" className="mt-0">
            <p className="leading-relaxed">
              Each justice&apos;s position is estimated from how they voted beside the others, case
              by case. The justice in the middle is the median: on a split Court, the vote a
              five-justice majority usually needs.
            </p>
            <p className="mt-2 leading-relaxed">
              An estimate, not a measurement: the interval is where the position lies with 95%
              probability under Martin and Quinn&apos;s model, which lets each justice&apos;s
              position move from term to term. Positions compare justices with one another; 0 is not
              a neutral point in law.
            </p>
          </More>
        </>
      )}
      <div className="flex justify-end">
        <ShareSectionButton label="Where the justices sit" />
      </div>
    </section>
  );
}
