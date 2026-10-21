import Link from "next/link";
import type { Metadata } from "next";
import { connection } from "next/server";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describePresidents } from "@/lib/seo";
import { fetchPresidents } from "@/lib/presidentServer";
import { EXECUTIVE_HREF, PRESIDENTS_HREF } from "@/lib/routes";
import { presidentParty } from "@/lib/presidentParty";
import { ordinal } from "@/components/scorecard/format";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import type { President } from "@/types/president";
import ScrollRegion from "@/components/shared/ScrollRegion";

export const metadata: Metadata = pageMetadata({ ...describePresidents(), path: PRESIDENTS_HREF });

const LINK = "underline underline-offset-2 hover:text-phos";

/** "2021–2025", "since 2025": the years a presidency ran. */
function term(p: President): string {
  const from = p.termStart.slice(0, 4);
  return p.termEnd ? `${from} to ${p.termEnd.slice(0, 4)}` : `since ${from}`;
}

/** The historians' place for one presidency, as the survey gives it, or
 *  why there is none: the survey rates completed terms only. */
function rank(p: President): string {
  if (p.historiansRank != null && p.historiansRated) {
    return `${ordinal(p.historiansRank)} of ${p.historiansRated}`;
  }
  return p.isCurrent ? "Not rated: in office" : "Not rated";
}

/**
 * Every president (`/executive/presidents`): the sitting one first, then
 * back to Washington, each with the historians' rank (C-SPAN's survey,
 * their ranking, attributed; the backend computes the place). Each name
 * opens the president's record; compare presidents puts two side by side.
 * A presidency is a row, so the two non-consecutive terms of one person are
 * two rows, as the survey rates them.
 */
export default async function PresidentsPage() {
  // Per request (fetches still cached): a build has no backend to read.
  await connection();
  const presidents = await fetchPresidents();
  return (
    <>
      <JsonLd
        data={[
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Executive", url: absoluteUrl(EXECUTIVE_HREF) },
            { name: "Every president", url: absoluteUrl(PRESIDENTS_HREF) },
          ]),
        ]}
      />
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto max-w-5xl">
            <SectionTabs section="executive" active="presidents" />
            <PageMasthead className="mb-8" eyebrow="Executive · presidents" title="Every president">
              <p>
                Each presidency, the sitting president first, with where historians rank it. Open a
                name for the record, or{" "}
                <Link href="/compare/presidents" className={LINK}>
                  compare two presidents
                </Link>
                .
              </p>
            </PageMasthead>
            {presidents.length === 0 ? (
              <p className="text-ink-lo">
                No president is on record yet: the roster hasn&apos;t been read.
              </p>
            ) : (
              // Three columns, so the table fits a phone: the number and party
              // sit under the name. At 200% text it can't, so it scrolls in
              // its own box rather than widening the page (WCAG 1.4.10).
              <ScrollRegion label="Every president, with the historians' rank">
                <table className="w-full border border-white/15 text-left text-sm">
                  <caption className="px-3 py-2 text-left text-sm text-ink-lo">
                    Historians&apos; rank: C-SPAN Presidential Historians Survey (2021), their
                    ranking by total points among {presidents[0]?.historiansRated ?? "the"} rated
                    presidencies. A sitting or just-departed president is not yet rated.
                  </caption>
                  <thead className="border-y border-white/15 font-mono text-xs text-ink-min">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-normal">
                        President
                      </th>
                      <th scope="col" className="px-3 py-2 font-normal">
                        In office
                      </th>
                      <th scope="col" className="px-3 py-2 font-normal">
                        Historians&apos; rank
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {presidents.map((p) => (
                      <tr key={p.id} className="border-b border-white/[0.07] last:border-0">
                        <th scope="row" className="px-3 py-2 font-normal">
                          <Link
                            href={`/politicians/${encodeURIComponent(p.id)}`}
                            className={`inline-flex min-h-11 items-center ${LINK}`}
                          >
                            {p.name}
                          </Link>
                          <span className="block text-xs text-ink-lo">
                            {ordinal(p.number)} · {presidentParty(p.party).label}
                          </span>
                        </th>
                        <td className="px-3 py-2 tabular-nums text-ink">{term(p)}</td>
                        <td className="px-3 py-2 tabular-nums text-ink">{rank(p)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollRegion>
            )}
          </div>
        </main>
        <Footer />
      </div>
    </>
  );
}
