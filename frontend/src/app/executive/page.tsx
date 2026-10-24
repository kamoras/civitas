import Link from "@/components/Link";
import type { Metadata } from "next";
import { connection } from "next/server";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeExecutive } from "@/lib/seo";
import { fetchPresidents } from "@/lib/presidentServer";
import { fetchAgencyIndex } from "@/lib/agencyServer";
import { exploreHref } from "@/lib/exploreHref";
import { calendarDate } from "@/lib/congress";
import { NOMINATIONS_HREF, nominationHref } from "@/lib/nominations";
import {
  AGENCY_BUDGETS_HREF,
  EXECUTIVE_HREF,
  EXECUTIVE_ORDERS_HREF,
  OPEN_COMMENTS_HREF,
  PRESIDENTS_HREF,
} from "@/lib/routes";
import { ordinal } from "@/components/scorecard/format";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import SectionCards, { fetchSectionFigures } from "@/components/sections/SectionCards";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import { formatCurrency } from "@/lib/formatting";
import BudgetBars from "@/components/agencies/BudgetBars";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import type { OpenCommentItem } from "@/lib/api";
import type { NominationList } from "@/types/nomination";
import type { PoliticianProfile } from "@/types/politicians";

export const metadata: Metadata = pageMetadata({ ...describeExecutive(), path: EXECUTIVE_HREF });

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const REVALIDATE_S = 300;

// How many of each the hub shows; each list's own page has the rest.
const ACTIONS_SHOWN = 5;
const COMMENTS_SHOWN = 3;
const AGENCIES_SHOWN = 5;
const NOMINATIONS_SHOWN = 3;

const LINK = "underline underline-offset-2 hover:text-phos";

/** One panel's data, or null when its source can't answer right now: the
 *  hub shows the other panels and says which one is missing, rather than
 *  failing the page over one of five reads. */
async function attempt<T>(read: () => Promise<T>): Promise<T | null> {
  try {
    return await read();
  } catch {
    return null;
  }
}

async function json<T>(path: string): Promise<T> {
  const res = await fetch(`${BACKEND}${path}`, { next: { revalidate: REVALIDATE_S } });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** A box's list behind its "More": each item a linked title over a line. */
function MoreList({
  items,
}: {
  items: { key: string; href: string; title: string; line: string }[];
}) {
  return (
    <ul>
      {items.map((it) => (
        <li key={it.key} className="border-b border-white/[0.07] py-1.5 last:border-0">
          <Link href={it.href} className={`text-small ${LINK}`}>
            {it.title}
          </Link>
          <span className="block font-mono text-small text-ink-min">{it.line}</span>
        </li>
      ))}
    </ul>
  );
}

/** What a box says for a source that can't answer right now: the hub shows
 *  the others rather than failing the page over one of five reads. */
function unavailable(what: string): string {
  return `${what} can't be read right now. Try again shortly.`;
}

/**
 * The executive branch's hub (`/executive`), led by stored records rather
 * than a list of links: the sitting president and their latest documents,
 * the comment periods most worth a reader's time (the Action Center's
 * salience order), the largest agencies by budget, and the nominations
 * waiting on the Senate. Each panel opens its full page; the section's
 * other pages follow as cards.
 */
export default async function ExecutivePage() {
  // Per request (fetches still cached): a build has no backend to read.
  await connection();
  const [presidents, comments, agencies, nominations, figures] = await Promise.all([
    attempt(fetchPresidents),
    attempt(() => json<OpenCommentItem[]>("/api/action/open-comments")),
    attempt(fetchAgencyIndex),
    attempt(() => json<NominationList>("/api/nominations?status=pending&per_page=3")),
    fetchSectionFigures(),
  ]);
  const openCount = figures?.agencies.comments.open ?? null;
  const budget = figures?.agencies.budget ?? null;
  const president = presidents?.find((p) => p.isCurrent) ?? null;
  const profile = president
    ? await attempt(() =>
        json<PoliticianProfile>(`/api/politicians/${encodeURIComponent(president.id)}`)
      )
    : null;
  const actions = profile?.governmentRecord?.recentDocs.slice(0, ACTIONS_SHOWN) ?? null;
  const budgetRows = agencies?.budget?.agencies.filter((r) => r.reported).slice(0, AGENCIES_SHOWN);

  return (
    <>
      <JsonLd
        data={[
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Executive", url: absoluteUrl(EXECUTIVE_HREF) },
          ]),
        ]}
      />
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto max-w-5xl">
            <SectionTabs section="executive" active={null} />
            <PageMasthead
              className="mb-6"
              eyebrow="Executive · Article II"
              title="The executive branch"
              more="This page shows what the president has signed, what the agencies spend, the rules open for your comment, and the nominations waiting on the Senate."
            >
              The president and the federal agencies that carry out the law.
            </PageMasthead>

            {/* Each part of the branch as a box led by its figure (every
                number the backend's: the president's documents on record,
                the open comment periods and the budget total from GET
                /api/sections, the pending nominations); the lists behind
                each box's More, each list's full page as a chip. */}
            <BoxGrid columns={2} className="mb-8">
              <RecordBox
                kicker="The President"
                level={2}
                headingAttrs={{ id: "president-heading" }}
                title={
                  president ? (
                    <Link href={`/politicians/${encodeURIComponent(president.id)}`}>
                      {president.name}
                    </Link>
                  ) : (
                    "The President"
                  )
                }
                dek={
                  !presidents
                    ? unavailable("The presidents")
                    : president
                      ? `${ordinal(president.number)} president, in office since ${calendarDate(president.termStart)}`
                      : "No sitting president is on record yet."
                }
                figure={
                  profile?.governmentRecord
                    ? {
                        type: "stat",
                        value: profile.governmentRecord.totalDocs,
                        caption: "presidential documents in the Federal Register",
                      }
                    : null
                }
                links={[
                  { label: "Executive orders", href: EXECUTIVE_ORDERS_HREF },
                  { label: "Every president", href: PRESIDENTS_HREF },
                ]}
                more={
                  president
                    ? {
                        label: "the latest presidential documents",
                        children:
                          actions === null ? (
                            unavailable("The president's documents")
                          ) : actions.length === 0 ? (
                            <p className="text-small text-ink-lo">
                              None in the Federal Register for this term yet.
                            </p>
                          ) : (
                            <MoreList
                              items={actions.map((d) => ({
                                key: String(d.id),
                                href: exploreHref(d),
                                title: d.title,
                                line: `${d.docType} · ${calendarDate(d.date)}`,
                              }))}
                            />
                          ),
                      }
                    : null
                }
              />

              <RecordBox
                kicker="Open for your comment"
                tone="deadline"
                level={2}
                headingAttrs={{ id: "comments-heading" }}
                title="Rules and notices taking comments"
                dek={
                  !comments
                    ? unavailable("The comment periods")
                    : comments.length === 0
                      ? "No Federal Register document takes public comments today."
                      : undefined
                }
                figure={
                  openCount !== null
                    ? { type: "stat", value: openCount, caption: "open for public comment today" }
                    : null
                }
                links={[{ label: "Every open comment period", href: OPEN_COMMENTS_HREF }]}
                more={
                  comments?.length
                    ? {
                        label: "three to comment on",
                        children: (
                          <MoreList
                            items={comments.slice(0, COMMENTS_SHOWN).map((c) => ({
                              key: String(c.id),
                              href: exploreHref(c),
                              title: c.title,
                              line: `${c.agencyName ? `${c.agencyName} · ` : ""}comments close ${calendarDate(c.commentsCloseOn)}`,
                            }))}
                          />
                        ),
                      }
                    : null
                }
              />

              <RecordBox
                kicker="The largest agencies"
                level={2}
                headingAttrs={{ id: "agencies-heading" }}
                title={
                  agencies?.budget
                    ? `Budgetary resources, fiscal year ${agencies.budget.fiscalYear}`
                    : "Agency budgets"
                }
                dek={
                  !agencies
                    ? unavailable("The agency budgets")
                    : !budgetRows?.length
                      ? "USAspending.gov's budgets haven't been read yet."
                      : undefined
                }
                figure={
                  budget
                    ? {
                        type: "stat",
                        value: budget.total,
                        text: formatCurrency(budget.total),
                        caption: `across ${budget.reportedCount} reporting agencies`,
                      }
                    : null
                }
                links={[{ label: "Every agency's budget", href: AGENCY_BUDGETS_HREF }]}
                below={budgetRows?.length ? <BudgetBars rows={budgetRows} /> : undefined}
              />

              <RecordBox
                kicker="Waiting on the Senate"
                level={2}
                headingAttrs={{ id: "nominations-heading" }}
                title="Pending nominations"
                dek={
                  !nominations
                    ? unavailable("The nominations")
                    : nominations.counts.pending === 0
                      ? "No nomination is pending in the Senate this Congress."
                      : undefined
                }
                figure={
                  nominations && nominations.counts.pending > 0
                    ? {
                        type: "stat",
                        value: nominations.counts.pending,
                        caption: `${nominations.counts.pending === 1 ? "nomination" : "nominations"} pending in the Senate`,
                      }
                    : null
                }
                links={[
                  { label: "Every pending nomination", href: `${NOMINATIONS_HREF}?status=pending` },
                ]}
                more={
                  nominations?.items.length
                    ? {
                        label: "the latest received",
                        children: (
                          <MoreList
                            items={nominations.items.slice(0, NOMINATIONS_SHOWN).map((n) => ({
                              key: n.citation,
                              href: nominationHref(n),
                              title: n.nominees.join(", ") || n.citation,
                              line: `${[n.positionTitle, n.organization].filter(Boolean).join(", ")} · received ${calendarDate(n.receivedDate)}`,
                            }))}
                          />
                        ),
                      }
                    : null
                }
              />
            </BoxGrid>

            <SectionCards section="executive" />
          </div>
        </main>
        <Footer />
      </div>
    </>
  );
}
