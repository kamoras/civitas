import Link from "next/link";
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
import SectionCards from "@/components/sections/SectionCards";
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
const MORE = "inline-flex min-h-11 items-center font-mono text-xs text-ink-lo hover:text-phos";

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

function Panel({
  id,
  title,
  more,
  children,
}: {
  id: string;
  title: string;
  more: { href: string; label: string };
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-heading`} className="border border-white/15 bg-surface p-4">
      <h2 id={`${id}-heading`} className="font-display text-xl font-semibold text-ink-hi">
        {title}
      </h2>
      <div className="mt-3">{children}</div>
      <Link href={more.href} className={`mt-2 ${MORE}`}>
        {more.label} →
      </Link>
    </section>
  );
}

function Unavailable({ what }: { what: string }) {
  return (
    <p className="text-sm text-ink-lo">{what} can&apos;t be read right now. Try again shortly.</p>
  );
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
  const [presidents, comments, agencies, nominations] = await Promise.all([
    attempt(fetchPresidents),
    attempt(() => json<OpenCommentItem[]>("/api/action/open-comments")),
    attempt(fetchAgencyIndex),
    attempt(() => json<NominationList>("/api/nominations?status=pending&per_page=3")),
  ]);
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
            <SectionTabs section="executive" active="overview" />
            <PageMasthead
              className="mb-8"
              eyebrow="Executive · Article II"
              title="The executive branch"
            >
              <p>
                The president and the federal agencies that carry out the law: what the president
                has signed, what the agencies spend, the rules open for your comment, and the
                nominations waiting on the Senate.
              </p>
            </PageMasthead>

            <div className="grid gap-4 md:grid-cols-2">
              <Panel
                id="president"
                title="The President"
                more={{ href: PRESIDENTS_HREF, label: "Every president" }}
              >
                {!presidents ? (
                  <Unavailable what="The presidents" />
                ) : !president ? (
                  <p className="text-sm text-ink-lo">No sitting president is on record yet.</p>
                ) : (
                  <>
                    <p className="text-lg">
                      <Link
                        href={`/politicians/${encodeURIComponent(president.id)}`}
                        className={LINK}
                      >
                        {president.name}
                      </Link>
                    </p>
                    <p className="text-sm text-ink-lo">
                      {ordinal(president.number)} president, in office since{" "}
                      {calendarDate(president.termStart)}
                    </p>
                    <h3 className="mt-4 font-mono text-xs text-ink-min">
                      Latest presidential documents
                    </h3>
                    {actions === null ? (
                      <Unavailable what="The president's documents" />
                    ) : actions.length === 0 ? (
                      <p className="text-sm text-ink-lo">
                        None in the Federal Register for this term yet.
                      </p>
                    ) : (
                      <ul className="mt-1">
                        {actions.map((d) => (
                          <li
                            key={d.id}
                            className="border-b border-white/[0.07] py-1.5 last:border-0"
                          >
                            <Link href={exploreHref(d)} className={`text-sm ${LINK}`}>
                              {d.title}
                            </Link>
                            <span className="block font-mono text-xs text-ink-min">
                              {d.docType} · {calendarDate(d.date)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <Link href={EXECUTIVE_ORDERS_HREF} className={`mt-2 ${MORE}`}>
                      Every executive order and proclamation →
                    </Link>
                  </>
                )}
              </Panel>

              <Panel
                id="comments"
                title="Open for your comment"
                more={{ href: OPEN_COMMENTS_HREF, label: "Every open comment period" }}
              >
                {!comments ? (
                  <Unavailable what="The comment periods" />
                ) : comments.length === 0 ? (
                  <p className="text-sm text-ink-lo">
                    No Federal Register document takes public comments today.
                  </p>
                ) : (
                  <ul>
                    {comments.slice(0, COMMENTS_SHOWN).map((c) => (
                      <li key={c.id} className="border-b border-white/[0.07] py-1.5 last:border-0">
                        <Link href={exploreHref(c)} className={`text-sm ${LINK}`}>
                          {c.title}
                        </Link>
                        <span className="block font-mono text-xs text-ink-min">
                          {c.agencyName ? `${c.agencyName} · ` : ""}comments close{" "}
                          {calendarDate(c.commentsCloseOn)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel
                id="agencies"
                title="The largest agencies"
                more={{ href: "/agencies#budget", label: "Every agency's budget" }}
              >
                {!agencies ? (
                  <Unavailable what="The agency budgets" />
                ) : !budgetRows?.length ? (
                  <p className="text-sm text-ink-lo">
                    USAspending.gov&apos;s budgets haven&apos;t been read yet.
                  </p>
                ) : (
                  <>
                    <p className="text-sm text-ink-lo">
                      Budgetary resources, fiscal year {agencies.budget!.fiscalYear}:
                    </p>
                    <BudgetBars rows={budgetRows} />
                  </>
                )}
              </Panel>

              <Panel
                id="nominations"
                title="Waiting on the Senate"
                more={{
                  href: `${NOMINATIONS_HREF}?status=pending`,
                  label: "Every pending nomination",
                }}
              >
                {!nominations ? (
                  <Unavailable what="The nominations" />
                ) : nominations.counts.pending === 0 ? (
                  <p className="text-sm text-ink-lo">
                    No nomination is pending in the Senate this Congress.
                  </p>
                ) : (
                  <>
                    <p className="text-sm text-ink-lo">
                      {nominations.counts.pending}{" "}
                      {nominations.counts.pending === 1 ? "nomination is" : "nominations are"}{" "}
                      pending in the Senate. The latest:
                    </p>
                    <ul className="mt-1">
                      {nominations.items.slice(0, NOMINATIONS_SHOWN).map((n) => (
                        <li
                          key={n.citation}
                          className="border-b border-white/[0.07] py-1.5 last:border-0"
                        >
                          <Link href={nominationHref(n)} className={`text-sm ${LINK}`}>
                            {n.nominees.join(", ") || n.citation}
                          </Link>
                          <span className="block font-mono text-xs text-ink-min">
                            {[n.positionTitle, n.organization].filter(Boolean).join(", ")}
                            {" · "}received {calendarDate(n.receivedDate)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </Panel>
            </div>

            <SectionCards section="executive" current="overview" />
          </div>
        </main>
        <Footer />
      </div>
    </>
  );
}
