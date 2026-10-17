import Link from "next/link";
import { connection } from "next/server";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import AgreementHeatmap from "@/components/court/AgreementHeatmap";
import CourtLine from "@/components/court/CourtLine";
import DecisionLineups from "@/components/court/DecisionLineups";
import RecentDecisions from "@/components/court/RecentDecisions";
import { courtLead, currentTermLine, COURT_HREF, COURT_SOURCES } from "@/lib/court";
import { COURT_FINDING, JUSTICE_RESEARCH_URL, NOT_SCORED_REASON } from "@/lib/justices";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { fetchRecord } from "@/lib/ssrPayload";
import type { Court, CourtDecisionPage } from "@/types/court";
import OnThisPage from "@/components/shared/OnThisPage";
import { onThisPage } from "@/lib/onThisPage";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const PER_PAGE = 10;

type Search = { page?: string };

function pageOf(sp: Search): number {
  return Math.min(Math.max(parseInt(sp.page ?? "1", 10) || 1, 1), 1000);
}

function hrefFor(page: number): string {
  return page > 1 ? `${COURT_HREF}?page=${page}#decisions` : `${COURT_HREF}#decisions`;
}

export async function generateMetadata({ searchParams }: { searchParams: Promise<Search> }) {
  return pageMetadata({
    title: "The Supreme Court: Justices, Votes and Decisions",
    description:
      "Where each Supreme Court justice sits left to right, how often each pair votes together, how the Court split term by term, and every recent decision's majority and dissent, from the Supreme Court Database, Oyez and Martin-Quinn scores.",
    path: COURT_HREF,
    // An older page of decisions is the same page further down: one URL.
    noindex: pageOf(await searchParams) > 1,
  });
}

const LINK = "underline decoration-white/25 underline-offset-4 hover:text-phos";

/**
 * The Supreme Court: who sits where, who votes with whom, how the Court
 * splits and what it decided, every figure the API's (court_service).
 * Records and measurements only: no justice is scored or ranked.
 */
export default async function CourtPage({ searchParams }: { searchParams: Promise<Search> }) {
  const page = pageOf(await searchParams);
  // Per request: `next build` can't reach the backend.
  await connection();
  const init = { next: { revalidate: 300 } };
  const court = await fetchRecord<Court>(`${BACKEND}/api/court`, init, "justices", "record");
  if (!court) throw new Error("Court: the record could not be read");
  const decisions =
    page > 1
      ? await fetchRecord<CourtDecisionPage>(
          `${BACKEND}/api/court/decisions?page=${page}&per_page=${PER_PAGE}`,
          init,
          "decisions"
        )
      : court.decisions;
  if (!decisions) throw new Error("Court: the decisions could not be read");
  const lead = courtLead(court);

  return (
    <ShareSubjectProvider
      subject={{
        title: "The Supreme Court",
        subtitle: court.justices.length ? `${court.justices.length} sitting justices` : undefined,
        url: absoluteUrl(COURT_HREF),
        source: COURT_SOURCES,
        asOf: null,
      }}
    >
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <OnThisPage />
          <div className="mx-auto flex max-w-5xl flex-col gap-8">
            <div>
              <SectionTabs section="court" active="court" />
              <PageMasthead eyebrow="Supreme Court · the record" title="The Supreme Court">
                <p>
                  Where the justices sit, who votes with whom and how they decided: the Court&apos;s
                  voting record, case by case.
                </p>
              </PageMasthead>
            </div>

            <section
              id="summary"
              aria-labelledby="summary-heading"
              {...onThisPage("The record in short")}
              {...{ [SHARE_SECTION_ATTR]: "summary" }}
              className="flex flex-col gap-3 border border-white/25 border-t-[3px] border-t-ink-hi bg-surface px-4 py-5 sm:px-6"
            >
              <h2 id="summary-heading" className="sr-only">
                The record in short
              </h2>
              {lead ? (
                <div className="flex flex-col gap-x-6 gap-y-2 sm:flex-row sm:items-center">
                  <p className="shrink-0 font-display text-6xl font-extrabold leading-none tabular-nums text-ink-hi sm:text-7xl">
                    {lead.figure}
                  </p>
                  <div className="flex flex-col gap-1">
                    <p className="max-w-prose text-xl font-semibold leading-snug text-ink-hi">
                      {lead.headline}
                    </p>
                    <p className="max-w-prose text-base leading-relaxed text-ink">{lead.detail}</p>
                  </div>
                </div>
              ) : (
                <p className="text-base text-ink">No decided case is stored yet.</p>
              )}
              <p role="status" className="font-mono text-sm text-ink-lo">
                {currentTermLine(court)}
              </p>
              <p className="text-sm leading-relaxed text-ink-lo">
                Justices are not scored or ranked. {NOT_SCORED_REASON}{" "}
                <a href={JUSTICE_RESEARCH_URL} className={LINK}>
                  The research
                </a>
              </p>
              <div className="flex justify-end">
                <ShareSectionButton label="The Supreme Court in short" />
              </div>
            </section>

            <CourtLine court={court} />
            <AgreementHeatmap court={court} />

            <section
              id="decisions"
              {...onThisPage("Decisions")}
              aria-labelledby="decisions-heading"
              className="flex flex-col gap-6 border border-white/25 bg-surface px-4 py-5 sm:px-6"
            >
              <h2 id="decisions-heading" className="text-xl font-bold text-ink-hi">
                Decisions
              </h2>
              <DecisionLineups court={court} />
              <div className="flex flex-col gap-3">
                <h3 className="text-lg font-bold text-ink-hi">Latest decisions and who joined</h3>
                <p
                  className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-lo"
                  aria-hidden="true"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-3 w-4 bg-ink-hi" /> majority
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-3 w-4 border-2 border-signal-red" /> dissent
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="h-3 w-4 border border-dashed border-white/30" /> did not take
                    part
                  </span>
                </p>
                <RecentDecisions page={decisions} justices={court.justices} hrefFor={hrefFor} />
              </div>
            </section>

            <section
              id="methods"
              {...onThisPage("Methods and sources")}
              aria-labelledby="methods-heading"
              className="flex flex-col gap-3 border border-white/25 bg-surface px-4 py-5 text-base leading-relaxed text-ink sm:px-6"
            >
              <h2 id="methods-heading" className="text-xl font-bold text-ink-hi">
                Methods and sources
              </h2>
              <p>
                <strong className="text-ink-hi">The cases.</strong> Orally argued cases the Court
                decided, with each justice&apos;s side and the opinions they wrote: from the Supreme
                Court Database for every term its newest release covers, and from Oyez for a term
                after that. Unanimous means no justice dissented; a case a justice sat out counts
                the justices who voted (8&ndash;0).
              </p>
              <p>
                <strong className="text-ink-hi">Agreement.</strong> For each pair, the share of the
                cases both took part in where they were on the same side, majority or dissent. A
                concurrence is on the majority&apos;s side.
              </p>
              <p>
                <strong className="text-ink-hi">Martin-Quinn positions.</strong> Andrew Martin and
                Kevin Quinn&apos;s estimate of each justice&apos;s position per term, from the votes
                of every justice in every case (Martin and Quinn, 2002, <em>Political Analysis</em>
                ), with its 95% credible interval. Published a term or more after the term ends, so
                it can trail the cases above.
              </p>
              <p>
                <strong className="text-ink-hi">Votes under the appointing president.</strong>{" "}
                {COURT_FINDING} Each justice&apos;s own estimate is on their page, with its
                interval, as information: a justice&apos;s years under the appointing president are
                always their first, and windows of the same length later in a career produce
                differences about as large. That is why no justice is scored.{" "}
                <Link href="/about/presidents-and-justices#justices" className={LINK}>
                  How justices are covered
                </Link>
              </p>
              <ul className="flex flex-col gap-1 text-sm text-ink-lo">
                <li>
                  <a href="https://scdb.la.psu.edu" className={LINK}>
                    The Supreme Court Database
                  </a>{" "}
                  (Spaeth, Epstein, Martin and others): Creative Commons Attribution-NonCommercial
                  3.0 United States.
                </li>
                <li>
                  <a href="https://www.oyez.org" className={LINK}>
                    Oyez
                  </a>
                  : Creative Commons Attribution-NonCommercial 4.0 International.
                </li>
                <li>
                  <a href="https://mqscores.wustl.edu" className={LINK}>
                    Martin-Quinn scores
                  </a>
                  : published by the authors with no licence stated; cited as Martin and Quinn
                  (2002).
                </li>
                <li>
                  Slip opinions:{" "}
                  <a href="https://www.supremecourt.gov/opinions/opinions.aspx" className={LINK}>
                    supremecourt.gov
                  </a>{" "}
                  (public domain).
                </li>
              </ul>
            </section>
          </div>
        </main>
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}
