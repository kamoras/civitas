"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import type { Senator } from "@/types/senator";
import type {
  AlignmentFacts,
  EffectivenessFacts,
  FundingFacts,
  RepresentationScoreBreakdown,
  ScoreBreakdownDimension,
} from "@/types/scoreBreakdown";
import type { RefId } from "@/components/about/references";
import { Cite } from "@/components/about/AboutPage";
import CiteThis from "@/components/shared/CiteThis";
import { formatCurrency } from "@/lib/formatting";
import { currentCongressLabel } from "@/lib/sources";
import { COMPUTED_LEAN_HREF, formatComputedLean, formatPvi } from "@/lib/elections";
import { onThisPage } from "@/lib/onThisPage";

type Dimension = "fundingIndependence" | "constituentAlignment" | "legislativeEffectiveness";

/** Each column's figures: what they are read from, over what period, and
 *  the research the method follows, keyed to the section of /about/scores
 *  that explains them. The same sources and citations /about/scores and
 *  /about/data give. */
const METHODS: {
  key: Dimension;
  name: string;
  anchor: string;
  cites: RefId[];
  source: (chamber: "senate" | "house") => string;
  window: (chamber: "senate" | "house") => string;
}[] = [
  {
    key: "fundingIndependence",
    name: "Money",
    anchor: "funding",
    cites: ["stratmann2005", "bonica2014", "rhoades1993"],
    source: () =>
      "FEC filings: committee totals and itemized contributions, with donors' industries classified by Civitas.",
    window: (chamber) =>
      `The member's most recent completed election (${chamber === "senate" ? "six years of fundraising" : "two years of fundraising"}).`,
  },
  {
    key: "constituentAlignment",
    name: "Votes",
    anchor: "alignment",
    cites: ["kirkland2017", "nokken2004", "ansolabehere2022"],
    source: (chamber) =>
      chamber === "senate"
        ? "Roll calls as Senate.gov records them; Voteview's Nokken-Poole positions; the state's lean, computed by Civitas with Cook's formula from certified presidential returns; Cooperative Election Study approval."
        : "Roll calls as the House Clerk records them; Voteview's Nokken-Poole positions; Cook PVI district lean; Cooperative Election Study approval.",
    window: () => `This Congress: the ${currentCongressLabel()}.`,
  },
  {
    key: "legislativeEffectiveness",
    name: "Bills",
    anchor: "effectiveness",
    cites: ["volden2014", "brin1998", "harbridgeyong2023"],
    source: () => "Congress.gov bill status and cosponsorships.",
    window: () => `This Congress: the ${currentCongressLabel()}.`,
  },
];

const GRADE_TEXT = {
  high: "Full",
  medium: "Partial",
  low: "Thin",
} as const;

const DT = "text-xs font-semibold text-ink-min";

function count(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** How much record the column's figures rest on, as counts from the
 *  breakdown's facts; null when the facts aren't there. */
function recordText(key: Dimension, dim: ScoreBreakdownDimension | undefined): string | null {
  const facts = dim?.facts;
  if (!facts) return null;
  if (key === "fundingIndependence") {
    const f = facts as unknown as FundingFacts;
    return f.contributions ? `${formatCurrency(f.contributions)} in contributions` : null;
  }
  if (key === "constituentAlignment") {
    const f = facts as unknown as AlignmentFacts;
    return f.partyVotes != null ? count(f.partyVotes, "party-line vote", "party-line votes") : null;
  }
  const f = facts as unknown as EffectivenessFacts;
  return f.bills != null ? count(f.bills, "bill sponsored", "bills sponsored") : null;
}

/** Records that changed after the nightly run, which the next run reads. */
function SinceNote({ dim }: { dim: ScoreBreakdownDimension | undefined }) {
  const since = dim?.sinceScored;
  if (!since) return null;
  const when = since.scoredAt ? since.scoredAt.replace("T", " ").replace("Z", " UTC") : null;
  const what =
    since.billsChanged != null && since.billsChanged > 0
      ? `${since.billsChanged} ${since.billsChanged === 1 ? "bill" : "bills"} changed`
      : "Records changed";
  return (
    <p className="border-l-2 border-signal-amber/60 pl-3 text-sm leading-relaxed text-ink-lo">
      {what} after {when ? `the run of ${when}` : "the run"}; the next nightly run includes them.
    </p>
  );
}

/** The seat lean the expected break rate is read from and, for a House
 * seat, Civitas's own computed lean beside it: both the API's figures. A
 * House seat's is Cook's published PVI; a state's is computed by Civitas
 * with Cook's formula (backend state_pvi.json), so it is not called Cook's. */
export function SeatLean({
  lean,
  chamber,
}: {
  lean?: AlignmentFacts["seatLean"];
  chamber: "senate" | "house";
}) {
  if (!lean) return null;
  return (
    <>
      <dt className={DT}>Seat lean</dt>
      <dd className="text-ink-lo">
        {chamber === "senate" ? "State lean " : "Cook PVI "}
        <span className="font-mono text-ink">{formatPvi(lean.cookPvi)}</span>
        {chamber === "senate"
          ? ", computed by Civitas with Cook's formula from certified presidential returns, the figure the expected rate uses"
          : ", the figure the expected rate uses"}
        {lean.computedLean != null && (
          <>
            ; Civitas-computed{" "}
            <span className="font-mono text-ink">{formatComputedLean(lean.computedLean)}</span>,
            shown for comparison (
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
 * The profile's expert layer, one link from the header ("methods, data &
 * citation"): for each column, where its figures come from, over what
 * period, how much record is behind them, the research the method follows
 * and whether records changed since the nightly run; then the record as
 * JSON from the public API, and a reference to cite. No 0-100 score
 * (redesign 2026-10): those stay only in the deprecated API fields.
 */
export default function MethodsLedger({
  member,
  chamber,
  breakdown,
  scoredOn,
}: {
  member: Senator;
  chamber: "senate" | "house";
  breakdown: RepresentationScoreBreakdown | null;
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
  const grades = member.representationScore.confidence ?? {};
  const api = `/api/public/v1/${chamber === "senate" ? "senators" : "representatives"}/${encodeURIComponent(member.id)}`;
  const path = `/politicians/${encodeURIComponent(member.id)}`;
  const office = chamber === "senate" ? "Senator" : "Representative";
  const version = scoredOn?.algorithmVersion ? `, method ${scoredOn.algorithmVersion}` : "";

  return (
    <section
      ref={ref}
      id="methods"
      {...onThisPage("Methods and citation")}
      className="border border-white/25 bg-surface font-sans"
    >
      {/* The toggle inside the heading, as CollapsibleSection does it: a
          heading inside a button drops out of the heading list. */}
      <h2 className="text-[19px] font-semibold leading-tight text-ink-hi">
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
          <span className="text-xs font-normal text-ink-min">
            {scoredOn ? `Updated ${scoredOn.date}` : "Not yet updated"}
            {scoredOn?.algorithmVersion ? ` · method ${scoredOn.algorithmVersion}` : ""} ·{" "}
            {currentCongressLabel()}
          </span>
        </button>
      </h2>

      <div id={bodyId} hidden={!open} className="flex flex-col gap-8 px-5 py-5">
        <p className="max-w-3xl text-sm leading-relaxed text-ink-lo">
          Every figure on this page is read from public records by fixed rules, with no model
          judging the member, and refreshed each night. &ldquo;Record behind it&rdquo; says how much
          record the figures rest on (full, partial or thin, by fixed counts of votes, bills and
          donors).{" "}
          <Link href="/about/scores" className="text-signal-cyan underline underline-offset-2">
            How each figure is measured
          </Link>
          .
        </p>

        {METHODS.map((m) => {
          const dim = breakdown?.[m.key];
          const grade = grades[m.key];
          const record = recordText(m.key, dim);
          return (
            <article
              key={m.key}
              className="flex flex-col gap-3 border-t border-white/[0.09] pt-5 first-of-type:border-t-0 first-of-type:pt-0"
            >
              <h3 className="font-display text-lg font-semibold text-ink-hi">{m.name}</h3>
              <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[8rem_minmax(0,1fr)]">
                <dt className={DT}>Source</dt>
                <dd className="text-ink-lo">{m.source(chamber)}</dd>
                <dt className={DT}>Period</dt>
                <dd className="text-ink-lo">{m.window(chamber)}</dd>
                {(record || grade) && (
                  <>
                    <dt className={DT}>Record behind it</dt>
                    <dd className="text-ink-lo">
                      {[record, grade && GRADE_TEXT[grade]].filter(Boolean).join(" · ")}
                    </dd>
                  </>
                )}
                {m.key === "constituentAlignment" && (
                  <SeatLean
                    lean={(dim?.facts as AlignmentFacts | undefined)?.seatLean}
                    chamber={chamber}
                  />
                )}
                <dt className={DT}>Method</dt>
                <dd className="text-ink-lo">
                  <Link
                    href={`/about/scores#${m.anchor}`}
                    className="underline decoration-white/30 underline-offset-4 hover:text-phos"
                  >
                    How the {m.name.toLowerCase()} figures are measured
                  </Link>
                  {m.cites.map((id) => (
                    <Cite key={id} id={id} />
                  ))}
                </dd>
              </dl>
              <SinceNote dim={dim} />
            </article>
          );
        })}

        <div className="grid grid-cols-1 gap-8 border-t border-white/[0.09] pt-5 lg:grid-cols-2">
          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-ink-min">The record as data</h3>
            <ul className="flex flex-col text-sm">
              {[
                [api, "Full record: funding, voting totals, data grades (JSON)"],
                [
                  `${api}/breakdown`,
                  "The figures, where the member stands among peers, and the reference values (JSON)",
                ],
                [
                  `${api}/votes?per_page=100`,
                  "Every vote this Congress, with its roll call (JSON)",
                ],
                [`${api}/votes.csv`, "Every vote this Congress, as a spreadsheet (CSV)"],
                [`${api}/history`, "The figures after every nightly run (JSON)"],
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
            <h3 className="text-sm font-semibold text-ink-min">Cite this record</h3>
            <CiteThis
              title={`${member.name}, ${office}: record among peers, ${currentCongressLabel()}${version}`}
              date={scoredOn?.date}
              path={path}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
