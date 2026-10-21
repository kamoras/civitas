import Link from "next/link";
import { onThisPage } from "@/lib/onThisPage";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { overlay } from "@/lib/chartColors";
import { JUSTICE_RESEARCH_URL, signedPoints } from "@/lib/justices";
import { SHARE_SECTION_ATTR, shareMetaAttrs } from "@/lib/shareImage";
import type { JusticeFigures } from "@/types/justice";

/** The axis, in points either way: the sitting justices' estimates fall
 *  within +-20 and their 95% intervals mostly within +-35
 *  (docs/research/justice-scores.md); one beyond is drawn at the edge. */
const AXIS_POINTS = 40;

const at = (share: number) =>
  `${((Math.max(-AXIS_POINTS, Math.min(AXIS_POINTS, share * 100)) + AXIS_POINTS) / (2 * AXIS_POINTS)) * 100}%`;

/**
 * Each sitting justice's appointer estimate with its 95% interval, one row
 * each, this justice's row marked. One neutral colour: an interval that
 * crosses 0 is not distinguishable from no difference, and none of it is a
 * measure of loyalty, which the line under the chart says.
 */
export default function AppointerIntervals({
  appointer,
  justiceId,
}: {
  appointer: NonNullable<JusticeFigures["appointer"]>;
  justiceId: string;
}) {
  return (
    <section
      id="appointer"
      aria-labelledby="appointer-heading"
      {...{ [SHARE_SECTION_ATTR]: "appointer" }}
      {...shareMetaAttrs({
        source: `Supreme Court Database${appointer.throughTerm ? ` through the ${appointer.throughTerm} term` : ""}, as Epstein and Posner (2016) measure it`,
        asOf: null,
      })}
      className="flex flex-col gap-3 border border-white/25 bg-surface px-4 py-5 font-sans sm:px-6"
    >
      <h2
        id="appointer-heading"
        {...onThisPage("Under the appointing president")}
        className="text-[19px] font-semibold text-ink-hi"
      >
        Votes for the government under the appointing president
      </h2>
      <p className="text-sm text-ink">
        Asks whether a justice sided with the federal government more while the appointing
        president&apos;s administration argued its cases.
      </p>
      <figure aria-labelledby="appointer-caption" className="flex flex-col gap-1">
        <div aria-hidden="true">
          {appointer.rows.map((r) => {
            const mine = r.id === justiceId;
            return (
              <div
                key={r.id}
                className="grid grid-cols-1 items-center gap-x-2 sm:grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)]"
              >
                <span
                  className={`truncate text-xs ${mine ? "font-semibold text-ink-hi" : "text-ink-lo"}`}
                >
                  {r.name}
                </span>
                <span className="relative block h-6">
                  <span
                    className="absolute left-1/2 top-0 h-6 border-l"
                    style={{ borderColor: overlay(0.4) }}
                  />
                  <span
                    className={`absolute top-[11px] h-0.5 ${mine ? "bg-ink-hi" : "bg-[var(--chart-muted)]"}`}
                    style={{ left: at(r.ciLow), right: `calc(100% - ${at(r.ciHigh)})` }}
                  />
                  <span
                    className={`absolute top-[6px] -ml-1.5 h-3 w-3 ${mine ? "bg-ink-hi outline outline-2 outline-[var(--phos)]" : "bg-[var(--chart-muted)]"}`}
                    style={{ left: at(r.estimate) }}
                  />
                </span>
              </div>
            );
          })}
          <div className="grid grid-cols-1 gap-x-2 text-xs text-ink-min sm:grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)]">
            <span className="hidden sm:block" />
            <span className="flex justify-between">
              <span>−{AXIS_POINTS}</span>
              <span>0</span>
              <span>+{AXIS_POINTS}</span>
            </span>
            <span className="hidden sm:block" />
            <span className="text-ink-lo">
              Points, right of 0: more often for the government under the appointing president.
            </span>
          </div>
        </div>
        <figcaption id="appointer-caption" className="text-sm text-ink">
          {appointer.sentence}
        </figcaption>
        <div className="sr-only">
          <table>
            <caption>
              Each sitting justice&apos;s estimate, in points, with its 95% interval
            </caption>
            <thead>
              <tr>
                <th scope="col">Justice</th>
                <th scope="col">Estimate</th>
                <th scope="col">95% interval</th>
              </tr>
            </thead>
            <tbody>
              {appointer.rows.map((r) => (
                <tr key={r.id}>
                  <th scope="row">
                    <Link href={`/politicians/${encodeURIComponent(r.id)}`}>{r.name}</Link>
                  </th>
                  <td>{signedPoints(r.estimate)}</td>
                  <td>
                    {signedPoints(r.ciLow)} to {signedPoints(r.ciHigh)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </figure>
      <p className="text-sm text-ink-lo">
        Not a measure of loyalty: a justice&apos;s years under the appointing president are always
        the first, and same-length windows later in a career show differences about as large.{" "}
        <a href={JUSTICE_RESEARCH_URL} className="text-phos underline underline-offset-2">
          The research
        </a>
      </p>
      <div className="flex justify-end">
        <ShareSectionButton label="Votes under the appointing president" />
      </div>
    </section>
  );
}
