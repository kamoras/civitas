"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import type { Senator } from "@/types/senator";
import type { RepresentationScoreBreakdown, ScoreBreakdownDimension } from "@/types/scoreBreakdown";
import type { RefId } from "@/components/about/references";
import { Cite } from "@/components/about/AboutPage";
import CiteThis from "@/components/shared/CiteThis";
import { displayScore } from "@/lib/formatting";
import { getScoreColor } from "@/lib/representation";
import { currentCongressLabel } from "@/lib/sources";
import { COMPUTED_LEAN_HREF, formatComputedLean, formatPvi } from "@/lib/elections";
import type { AlignmentFacts } from "@/types/scoreBreakdown";

type Dimension =
  "fundingIndependence" | "constituentAlignment" | "legislativeEffectiveness" | "fundingDiversity";

/** What each scored part is read from and the research its method follows:
 *  the same sources and citations /about/scores and /about/data give, keyed
 *  to the section of /about/scores that explains the part in full. */
const METHODS: {
  key: Dimension;
  name: string;
  anchor: string;
  cites: RefId[];
  source: (chamber: "senate" | "house") => string;
}[] = [
  {
    key: "fundingIndependence",
    name: "Funding Independence",
    anchor: "funding",
    cites: ["stratmann2005", "bonica2014", "rhoades1993"],
    source: () =>
      "FEC filings for the member's most recent completed election: committee totals and itemized contributions.",
  },
  {
    key: "constituentAlignment",
    name: "Constituent Alignment",
    anchor: "alignment",
    cites: ["kirkland2017", "nokken2004", "ansolabehere2022"],
    source: (chamber) =>
      chamber === "senate"
        ? "Roll calls this Congress as Senate.gov records them; Voteview's Nokken-Poole positions; Cook PVI state lean; Cooperative Election Study approval."
        : "Roll calls this Congress as the House Clerk records them; Voteview's Nokken-Poole positions; Cook PVI district lean.",
  },
  {
    key: "legislativeEffectiveness",
    name: "Legislative Effectiveness",
    anchor: "effectiveness",
    cites: ["volden2014", "brin1998", "harbridgeyong2023"],
    source: () => "Congress.gov bill status and cosponsorships, this Congress.",
  },
  {
    key: "fundingDiversity",
    name: "Funding Diversity (shown, not weighted)",
    anchor: "funding",
    cites: ["rhoades1993"],
    source: () => "The same FEC filings, with donors' industries classified by Civitas.",
  },
];

const GRADE_TEXT = {
  high: "High",
  medium: "Medium",
  low: "Low",
} as const;

const TH =
  "py-2 pr-3 text-left font-mono text-xs font-normal uppercase tracking-[0.12em] text-ink-min";

/** A signed number as the ledger prints it: one decimal, a true minus sign. */
function signed(n: number): string {
  return `${n < 0 ? "\u2212" : "+"}${Math.abs(n).toFixed(1)}`;
}

function Components({ dim, name }: { dim: ScoreBreakdownDimension; name: string }) {
  const components = dim.components;
  const hasPoints = components.some((c) => c.combines === "points" && c.points != null);
  // Focusable, so a keyboard can scroll a table wider than a phone.
  return (
    <div
      className="overflow-x-auto"
      tabIndex={0}
      role="region"
      aria-label={`${name}: what the score is made of`}
    >
      <table className="w-full min-w-[34rem] table-fixed border-collapse text-sm">
        <caption className="sr-only">What the score is made of</caption>
        <thead>
          <tr className="border-b border-white/[0.09]">
            <th scope="col" className={`${TH} w-[11rem]`}>
              Component
            </th>
            <th scope="col" className={`${TH} w-[5.5rem] text-right`}>
              Weight
            </th>
            <th scope="col" className={`${TH} w-[4rem] text-right`}>
              Score
            </th>
            <th scope="col" className={TH}>
              How it was measured
            </th>
          </tr>
        </thead>
        <tbody>
          {components.map((c) => (
            <tr key={c.label} className="border-b border-white/[0.07] align-top">
              <th scope="row" className="py-2 pr-3 text-left font-normal text-ink">
                {c.label}
              </th>
              <td className="py-2 pr-3 text-right font-mono tabular-nums text-ink-lo">
                {c.combines === "points" ? (
                  c.points != null ? (
                    <>
                      {signed(c.points)} pts
                      <span className="sr-only">
                        , {Math.round((c.weight ?? 0) * 100)}% of the score&apos;s distance from 50
                      </span>
                    </>
                  ) : (
                    "\u00b1 pts"
                  )
                ) : c.weight != null ? (
                  `${Math.round(c.weight * 100)}%`
                ) : (
                  "\u2014"
                )}
              </td>
              <td className="py-2 pr-3 text-right font-mono tabular-nums text-ink-hi">
                {c.score != null ? Math.round(c.score) : "n/a"}
              </td>
              <td className="py-2 leading-relaxed text-ink-lo">{c.detail}</td>
            </tr>
          ))}
        </tbody>
        {dim.sum != null && (
          <tfoot>
            <tr className="align-top">
              <th scope="row" className="py-2 pr-3 text-left font-normal text-ink">
                Total
              </th>
              <td />
              <td className="py-2 pr-3 text-right font-mono tabular-nums text-ink-hi">
                {dim.score}
              </td>
              <td className="py-2 leading-relaxed text-ink-lo">
                {hasPoints
                  ? "The weighted average of the parts given in percent, plus the points shown: "
                  : "The weighted average of the parts: "}
                <span className="font-mono text-ink">
                  {dim.sum < 0 ? `\u2212${Math.abs(dim.sum).toFixed(1)}` : dim.sum.toFixed(1)}
                </span>
                {dim.clamped
                  ? `. Scores run from 0 to 100, so it is held at ${dim.score}.`
                  : `, shown rounded as ${dim.score}.`}
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/** Why the arithmetic above might not be the last word: today's records
 *  would score the part differently (they changed after the run), or the
 *  parts were recomputed for a score stored before they were kept. */
function SinceScoredNote({ dim, shown }: { dim: ScoreBreakdownDimension; shown: number | null }) {
  const since = dim.sinceScored;
  if (since) {
    const when = since.scoredAt ? since.scoredAt.replace("T", " ").replace("Z", " UTC") : null;
    const what =
      since.billsChanged != null && since.billsChanged > 0
        ? `${since.billsChanged} ${since.billsChanged === 1 ? "bill" : "bills"} changed`
        : "Records changed";
    return (
      <p className="border-l-2 border-signal-amber/60 pl-3 text-sm leading-relaxed text-ink-lo">
        <span className="font-mono text-xs uppercase tracking-[0.12em] text-signal-amber">
          Updated since this score
        </span>{" "}
        {what} after {when ? `the run of ${when}` : "the run"}. On today&apos;s records this part
        would be {since.score}; the next nightly run includes them.
      </p>
    );
  }
  if (dim.basis === "recomputed" && shown != null && Math.round(dim.score) !== shown) {
    return (
      <p className="border-l-2 border-signal-amber/60 pl-3 text-sm leading-relaxed text-ink-lo">
        These parts are worked out from today&apos;s records and give {dim.score}: the score shown
        was stored before Civitas kept each run&apos;s own arithmetic. The next nightly run stores
        both together.
      </p>
    );
  }
  return null;
}

/** The seat lean the score read (Cook's PVI) and, for a House seat,
 * Civitas's own computed lean beside it — both the API's figures. */
function SeatLean({ lean }: { lean?: AlignmentFacts["seatLean"] }) {
  if (!lean) return null;
  return (
    <>
      <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">Seat lean</dt>
      <dd className="text-ink-lo">
        Cook PVI <span className="font-mono text-ink">{formatPvi(lean.cookPvi)}</span>, the figure
        the score uses
        {lean.computedLean != null && (
          <>
            ; Civitas-computed{" "}
            <span className="font-mono text-ink">{formatComputedLean(lean.computedLean)}</span>,
            shown for comparison and not scored (
            <Link
              href={COMPUTED_LEAN_HREF}
              className="underline underline-offset-2 hover:text-phos"
            >
              how it is computed
            </Link>
            )
          </>
        )}
        .
      </dd>
    </>
  );
}

/**
 * The scorecard's expert layer, one link from the header ("methods, data &
 * citation"): for each scored part, its score, how much data backs it, every
 * component with the scorer's own sentence saying how it was measured (the
 * sample sizes are in those sentences), the research the method follows and
 * where the data comes from; then the record as JSON from the public API,
 * and a reference to cite. Nothing here is computed: the scores, grades and
 * component sentences are the API's, and the sources and citations are the
 * ones /about/scores and /about/data give.
 */
export default function MethodsLedger({
  member,
  chamber,
  breakdown,
  weights,
  scoredOn,
}: {
  member: Senator;
  chamber: "senate" | "house";
  breakdown: RepresentationScoreBreakdown | null;
  weights?: Record<string, number>;
  scoredOn?: { date: string; algorithmVersion: string | null } | null;
}) {
  const ref = useRef<HTMLElement>(null);
  const bodyId = useId();
  const [open, setOpen] = useState(false);
  // Closed until asked for, so it costs a casual reader no scrolling; the
  // header's "methods, data & citation" link (#methods), or a link to it
  // from elsewhere, opens it.
  useEffect(() => {
    const openOnHash = () => {
      if (window.location.hash !== "#methods") return;
      setOpen(true);
      ref.current?.scrollIntoView();
    };
    // A second click on the link, after closing the section, changes no
    // hash: the click itself opens it then.
    const openOnLink = (e: MouseEvent) => {
      if ((e.target as Element | null)?.closest?.('a[href="#methods"]')) setOpen(true);
    };
    openOnHash();
    window.addEventListener("hashchange", openOnHash);
    document.addEventListener("click", openOnLink);
    return () => {
      window.removeEventListener("hashchange", openOnHash);
      document.removeEventListener("click", openOnLink);
    };
  }, []);
  const scores = member.representationScore;
  const grades = scores.confidence ?? {};
  const api = `/api/public/v1/${chamber === "senate" ? "senators" : "representatives"}/${encodeURIComponent(member.id)}`;
  const path = `/politicians/${encodeURIComponent(member.id)}`;
  const office = chamber === "senate" ? "Senator" : "Representative";
  const version = scoredOn?.algorithmVersion ? `, scoring method ${scoredOn.algorithmVersion}` : "";

  return (
    <section ref={ref} id="methods" className="border border-white/25 bg-surface font-sans">
      {/* The toggle inside the heading, as CollapsibleSection does it: a
          heading inside a button drops out of the heading list. */}
      <h2 className="text-[19px] font-bold leading-tight text-ink-hi">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-controls={bodyId}
          className={`flex min-h-[44px] w-full flex-wrap items-baseline justify-between gap-3 bg-surface-raised px-5 py-4 text-left hover:text-phos ${open ? "border-b border-white/25" : ""}`}
        >
          <span>
            <span aria-hidden="true" className="mr-2 font-mono text-ink-min">
              {open ? "−" : "+"}
            </span>
            Methods, data and citation
          </span>
          <span className="font-mono text-xs font-normal uppercase tracking-[0.12em] text-ink-min">
            {scoredOn ? `Scored ${scoredOn.date}` : "Not yet scored"}
            {scoredOn?.algorithmVersion ? ` · method ${scoredOn.algorithmVersion}` : ""} ·{" "}
            {currentCongressLabel()}
          </span>
        </button>
      </h2>

      <div id={bodyId} hidden={!open} className="flex flex-col gap-8 px-5 py-5">
        <p className="max-w-3xl text-sm leading-relaxed text-ink-lo">
          Each score is a deterministic formula over public records, with no model judging the
          member. &ldquo;Data behind it&rdquo; grades how much data the score rests on (high, medium
          or low, by fixed counts of votes, bills and donors): it is not a confidence interval, and
          none is computed. Where a part rests on little data, its method pulls it toward a middle
          value, 50 or the party&apos;s typical score, and says which. Parts given in percent are
          averaged by weight; a part given in points adds or takes that many points. Each total
          shows the sum before rounding.{" "}
          <Link href="/about/scores" className="text-signal-cyan underline underline-offset-2">
            The full method
          </Link>
          .
        </p>

        {METHODS.map((m) => {
          const value = scores[m.key];
          const shown = value == null ? null : displayScore(value);
          const grade = grades[m.key];
          const dim = breakdown?.[m.key];
          const weight = weights?.[m.key];
          return (
            <article
              key={m.key}
              className="flex flex-col gap-3 border-t border-white/[0.09] pt-5 first-of-type:border-t-0 first-of-type:pt-0"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h3 className="font-display text-lg font-semibold text-ink-hi">{m.name}</h3>
                <p className="flex flex-wrap items-baseline gap-x-4 gap-y-1 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                  <span>
                    Score{" "}
                    <span className={`text-sm ${shown == null ? "" : getScoreColor(shown)}`}>
                      {shown ?? "—"}
                    </span>
                  </span>
                  {weight != null && weight > 0 && (
                    <span>
                      Weight <span className="text-ink-hi">{Math.round(weight * 100)}%</span>
                    </span>
                  )}
                  {grade && (
                    <span>
                      Data behind it <span className="text-ink-hi">{GRADE_TEXT[grade]}</span>
                    </span>
                  )}
                </p>
              </div>
              {dim?.components?.length ? (
                <>
                  <Components dim={dim} name={m.name} />
                  <SinceScoredNote dim={dim} shown={shown} />
                </>
              ) : (
                <p className="text-sm text-ink-lo">
                  {dim?.note ?? "No breakdown is available for this score."}
                </p>
              )}
              <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[7rem_minmax(0,1fr)]">
                <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                  Method
                </dt>
                <dd className="text-ink-lo">
                  <Link
                    href={`/about/scores#${m.anchor}`}
                    className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                  >
                    How {m.name.replace(/ \(.*\)$/, "")} is computed
                  </Link>
                  {m.cites.map((id) => (
                    <Cite key={id} id={id} />
                  ))}
                </dd>
                <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                  Source
                </dt>
                <dd className="text-ink-lo">{m.source(chamber)}</dd>
                {m.key === "constituentAlignment" && (
                  <SeatLean lean={(dim?.facts as AlignmentFacts | undefined)?.seatLean} />
                )}
              </dl>
            </article>
          );
        })}

        <div className="grid gap-8 border-t border-white/[0.09] pt-5 lg:grid-cols-2">
          <div className="flex flex-col gap-2">
            <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
              The record as data
            </h3>
            <ul className="flex flex-col text-sm">
              {[
                [api, "Full record: scores, data grades, funding, voting totals (JSON)"],
                [
                  `${api}/breakdown`,
                  "Reproduce every score: components, weights and the reference values used (JSON)",
                ],
                [
                  `${api}/votes?per_page=100`,
                  "Every vote this Congress, with its roll call (JSON)",
                ],
                [`${api}/history`, "Scores after every scoring run (JSON)"],
              ].map(([href, label]) => (
                <li key={href} className="border-b border-white/[0.07] py-1.5">
                  <a
                    href={href}
                    className="text-ink underline decoration-white/30 underline-offset-4 hover:text-phos"
                  >
                    {label}
                  </a>
                </li>
              ))}
              <li className="py-1.5 text-ink-lo">
                No key needed.{" "}
                <Link
                  href="/developers"
                  className="text-ink underline decoration-white/30 underline-offset-4 hover:text-phos"
                >
                  API reference and MCP server
                </Link>{" "}
                ·{" "}
                <Link href="/about/data" className="underline underline-offset-2 hover:text-phos">
                  every data source
                </Link>
              </li>
            </ul>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
              Cite this scorecard
            </h3>
            <CiteThis
              title={`${member.name}, ${office}: Representation scorecard, ${currentCongressLabel()}${version}`}
              date={scoredOn?.date}
              path={path}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
