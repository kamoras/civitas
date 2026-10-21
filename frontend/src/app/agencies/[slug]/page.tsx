import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { absoluteUrl, agencyFeed, notFoundMetadata, pageMetadata } from "@/lib/site";
import FollowFeed from "@/components/shared/FollowFeed";
import { CARD_ALT, agencyJsonLd, cardImage, describeAgency } from "@/lib/seo";
import { fetchAgency } from "@/lib/agencyServer";
import { fiscalYearLabel, money, readOn } from "@/lib/agencies";
import { commentPeriodToday, safeHref } from "@/lib/formatting";
import { agencyDocumentsHref, agencyHref } from "@/lib/routes";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import BoxGrid from "@/components/shared/BoxGrid";
import RecordBox from "@/components/shared/RecordBox";
import AgencyDocList from "@/components/agencies/AgencyDocList";
import PaperworkFold from "@/components/shared/PaperworkFold";
import SourceLine from "@/components/agencies/SourceLine";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import type { AgencyDetail } from "@/types/agency";
import type { Figure as BoxFigure } from "@/types/figure";

type PageProps = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const agency = await fetchAgency(slug);
  if (!agency) return notFoundMetadata("Agency not found", "No federal agency by this name.");
  return pageMetadata({
    ...describeAgency(agency),
    path: agencyHref(agency.slug),
    feed: agencyFeed(agency.slug, agency.name),
    images: cardImage(`agency=${encodeURIComponent(agency.slug)}`, CARD_ALT.agency(agency.name)),
  });
}

// While documents are still being linked to their agencies, an empty list
// is not "none" (the outage-or-incomplete-is-not-none rule).
const LINKING_TAIL = "Older documents are still being linked to the agency that issued them.";

const H2 = "font-display text-xl font-semibold text-ink-hi";
const MORE =
  "mt-3 inline-flex min-h-6 items-center font-mono text-xs text-signal-cyan hover:text-phos";

function paragraphs(text: string | null): string[] {
  return (text ?? "")
    .split(/\r?\n\s*\r?\n/)
    .map((p) => p.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-xs text-ink-min">{label}</dt>
      <dd className="mt-1 font-display text-2xl font-semibold tabular-nums text-ink-hi">{value}</dd>
      {note && <dd className="text-xs text-ink-lo">{note}</dd>}
    </div>
  );
}

/** Budget figures by the state USAspending's data is in for this agency
 * (backend api/agencies._budget_status): each worded on its own, a year not
 * yet reported never shown as $0, and an outage never as "no budget". */
function BudgetBlock({ a }: { a: AgencyDetail }) {
  const b = a.budget;
  const status = a.budgetStatus;
  const LINK = "text-signal-cyan underline underline-offset-2 hover:text-phos";
  if (status === "part_of" && a.budgetOf) {
    return (
      <p className="mt-3 text-sm text-ink">
        Its spending is reported within the budget of the{" "}
        <Link href={agencyHref(a.budgetOf.slug)} className={LINK}>
          {a.budgetOf.name}
        </Link>
        .
      </p>
    );
  }
  if (status === "not_read" || !b) {
    return status === "unmatched" ? (
      <div className="mt-3 text-sm text-ink-lo">
        <p>
          No agency in USAspending.gov&apos;s list matched this one by name closely enough to link
          the two, so no budget is shown rather than a guessed one.
        </p>
        {a.unmatchedBudgets.length > 0 && (
          <details className="mt-2" data-share-exclude>
            <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs text-ink-min hover:text-phos">
              USAspending agencies left unlinked: {a.unmatchedBudgets.length}
            </summary>
            <p className="mt-1 text-xs leading-relaxed">{a.unmatchedBudgets.join("; ")}.</p>
          </details>
        )}
      </div>
    ) : (
      <p className="mt-3 text-sm text-ink-lo">
        USAspending.gov&apos;s budgets haven&apos;t been read yet, so none is shown. That is a gap
        on Civitas, not an agency without a budget.
      </p>
    );
  }
  const latest = b.latest;
  const current = b.reported;
  return (
    <>
      {status === "none" ? (
        <p className="mt-3 text-sm text-ink-lo">
          USAspending.gov lists {b.name} but reports no budget figures for it in any fiscal year.
        </p>
      ) : (
        <>
          {status === "retired" && (
            <p className="mt-3 text-sm text-ink-lo">
              USAspending.gov stopped listing {b.name} on {readOn(b.retiredAt)}. Its last reported
              figures:
            </p>
          )}
          {status === "not_yet_reported" && b.fiscalYear && (
            <p className="mt-3 text-sm text-ink-lo">
              FY{b.fiscalYear} not yet reported by USAspending.gov. The latest year it has:
            </p>
          )}
          <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Figure
              label="Budgetary resources"
              value={money(current ? b.budgetAuthority : latest?.budgetaryResources)}
              note={
                current
                  ? fiscalYearLabel(b.fiscalYear, b.throughPeriod)
                  : latest
                    ? fiscalYearLabel(latest.fiscalYear, latest.throughPeriod)
                    : ""
              }
            />
            <Figure
              label="Obligated"
              value={money(current ? b.obligated : latest?.obligated)}
              note="committed"
            />
            <Figure
              label="Outlays"
              value={money(current ? b.outlays : latest?.outlays)}
              note="paid out"
            />
          </dl>
        </>
      )}
      {b.byYear && b.byYear.length > 1 && (
        <div className="mt-5">
          <p className="font-mono text-xs text-ink-min">Budgetary resources by fiscal year</p>
          <ol className="mt-2 space-y-1.5">
            {b.byYear.map((y) => (
              <li
                key={y.fiscalYear}
                className="grid grid-cols-[minmax(0,7.5rem)_minmax(2rem,1fr)_auto] items-center gap-2 text-xs sm:grid-cols-[minmax(0,11rem)_minmax(2rem,1fr)_auto]"
              >
                <span className="font-mono text-ink-lo">
                  {fiscalYearLabel(y.fiscalYear, y.throughPeriod)}
                </span>
                <span className="h-2 bg-white/[0.05]" aria-hidden="true">
                  <span className="block h-2 bg-phos/70" style={{ width: `${y.barPercent}%` }} />
                </span>
                <span className="font-mono tabular-nums text-ink-lo">
                  {money(y.budgetaryResources)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <SourceLine
        source={b.source}
        asOf={b.asOf}
        detail={`${b.name}, ${
          current
            ? fiscalYearLabel(b.fiscalYear, b.throughPeriod)
            : latest
              ? `FY${b.fiscalYear} not yet reported; latest ${fiscalYearLabel(latest.fiscalYear, latest.throughPeriod)}`
              : "no fiscal year reported"
        }`}
      />
    </>
  );
}

/** The budget figure the box leads with: the reported year's budgetary
 * resources, or the latest year USAspending has. Null where there is no
 * figure to show (BudgetBlock words why). */
function budgetFigure(a: AgencyDetail): BoxFigure | null {
  const b = a.budget;
  if (!b || !["reported", "not_yet_reported", "retired"].includes(a.budgetStatus)) return null;
  const value = b.reported ? b.budgetAuthority : b.latest?.budgetaryResources;
  if (value == null) return null;
  const year = b.reported
    ? fiscalYearLabel(b.fiscalYear, b.throughPeriod)
    : b.latest
      ? fiscalYearLabel(b.latest.fiscalYear, b.latest.throughPeriod)
      : "";
  return { type: "stat", value, text: money(value), caption: year || "budgetary resources" };
}

/** What it spends, what it is doing and who it is, in boxes ("Boxes, not
 * blocks"): the figures first, then the Federal Register's description,
 * quoted by its first sentence with the whole of it behind "More". */
function Overview({ a }: { a: AgencyDetail }) {
  const docs = a.documents;
  const proposed = docs.byType["Proposed Rule"] ?? 0;
  const final = docs.byType["Final Rule"] ?? 0;
  const about = paragraphs(a.description);
  const budget = budgetFigure(a);
  const docsSource = (
    <SourceLine
      source={a.documentsSource}
      asOf={a.documentsAsOf}
      detail={`documents on Civitas${a.children.length ? ", its sub-agencies' included" : ""}`}
    />
  );
  return (
    <section id="overview" data-share-section="overview" className="mb-10">
      <BoxGrid columns={3} label="At a glance" labelId="overview-heading">
        <RecordBox
          kicker="Budget · USAspending.gov"
          title={budget ? "Budgetary resources" : "Budget"}
          figure={budget}
          more={
            budget
              ? { label: "obligations, outlays and each year", children: <BudgetBlock a={a} /> }
              : null
          }
        >
          {!budget && <BudgetBlock a={a} />}
        </RecordBox>
        <RecordBox
          kicker="Federal Register"
          title="Open for comment"
          figure={{ type: "stat", value: docs.openCommentsCount, caption: "periods open" }}
          links={
            docs.openCommentsCount > 0
              ? [
                  {
                    label: "The periods",
                    href: "#comment-heading",
                    ariaLabel: "Open comment periods",
                  },
                ]
              : undefined
          }
          more={{ label: "where the counts come from", children: docsSource }}
        >
          {docs.linking && (
            <p className="text-xs leading-relaxed text-ink-lo">
              Counts so far: documents stored before agencies were read are still being linked to
              the agency that issued them ({docs.linking.linked.toLocaleString("en-US")} of{" "}
              {docs.linking.total.toLocaleString("en-US")} linked).
            </p>
          )}
        </RecordBox>
        <RecordBox
          kicker="Federal Register"
          title="Proposed rules"
          figure={{ type: "stat", value: proposed, caption: "on Civitas" }}
          links={
            proposed > 0
              ? [
                  {
                    label: "The newest",
                    href: "#proposed-heading",
                    ariaLabel: "Newest proposed rules",
                  },
                ]
              : undefined
          }
        />
        <RecordBox
          kicker="Federal Register"
          title="Final rules"
          figure={{ type: "stat", value: final, caption: "on Civitas" }}
          links={
            final > 0
              ? [{ label: "The newest", href: "#final-heading", ariaLabel: "Newest final rules" }]
              : undefined
          }
        />
        {(a.descriptionLede || a.website || a.frUrl) && (
          <RecordBox
            id="about"
            kicker="Its description · Federal Register"
            title="About the agency"
            {...(a.descriptionLede
              ? {
                  quote: {
                    text: a.descriptionLede,
                    source: "The Federal Register's agency profile",
                  },
                }
              : {})}
            links={[
              ...(a.website ? [{ label: "Agency website", href: safeHref(a.website) || "#" }] : []),
              ...(a.frUrl
                ? [{ label: "On the Federal Register", href: safeHref(a.frUrl) || "#" }]
                : []),
            ]}
            more={
              about.length
                ? {
                    label: "the full description",
                    children: (
                      <>
                        {about.map((p, i) => (
                          <p key={i} className="mt-2 first:mt-0 max-w-3xl leading-relaxed">
                            {p}
                          </p>
                        ))}
                        <SourceLine
                          source={a.profileSource}
                          asOf={a.profileAsOf}
                          detail="description and organization"
                        />
                      </>
                    ),
                  }
                : null
            }
          />
        )}
      </BoxGrid>
      <div className="mt-2 flex justify-end">
        <ShareSectionButton label={`${a.name} at a glance`} />
      </div>
    </section>
  );
}

/** One agency's page: who it is, what it spends, the rules it proposes and
 * issues, and where to comment. Server-rendered; an unknown agency is a
 * real 404 and an outage an error page. */
export default async function AgencyPage({ params }: PageProps) {
  const { slug } = await params;
  const a = await fetchAgency(slug);
  if (!a) notFound();
  const today = commentPeriodToday();
  const docs = a.documents;
  const eos = a.executiveOrders;
  const path = agencyHref(a.slug);

  return (
    <ShareSubjectProvider
      subject={{
        title: a.name,
        subtitle: a.parent ? `Part of the ${a.parent.name}` : "Federal agency",
        url: absoluteUrl(path),
        source: a.budget ? "USAspending.gov; Federal Register" : "Federal Register",
        asOf: a.budget?.asOf ?? a.documentsAsOf,
      }}
    >
      <JsonLd
        data={[
          agencyJsonLd(a),
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Agencies", url: absoluteUrl("/agencies") },
            ...(a.parent
              ? [{ name: a.parent.name, url: absoluteUrl(agencyHref(a.parent.slug)) }]
              : []),
            { name: a.name, url: absoluteUrl(path) },
          ]),
        ]}
      />
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto max-w-5xl">
            <nav aria-label="Breadcrumb" className="mb-4 text-sm text-ink-lo">
              <Link href="/agencies" className="hover:text-phos">
                Agencies
              </Link>
              {a.parent && (
                <>
                  {" / "}
                  <Link href={agencyHref(a.parent.slug)} className="hover:text-phos">
                    {a.parent.name}
                  </Link>
                </>
              )}
              {" / "}
              <span aria-current="page">{a.shortName ?? a.name}</span>
            </nav>
            <PageMasthead
              className="mb-8"
              eyebrow={`Federal agency${a.shortName ? ` · ${a.shortName}` : ""}`}
              title={a.name}
            />
            {/* Its rules and comment periods as a feed, its sub-agencies' too. */}
            <FollowFeed className="-mt-4 mb-8" subject={a.name} feed={agencyFeed(a.slug, a.name)} />

            <Overview a={a} />

            <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
              <section aria-labelledby="comment-heading">
                <h2 id="comment-heading" className={H2}>
                  Open for comment
                </h2>
                {docs.openComments.length > 0 || (docs.paperworkCount ?? 0) > 0 ? (
                  <>
                    <AgencyDocList docs={docs.openComments} today={today} showDeadline />
                    <PaperworkFold
                      count={docs.paperworkCount ?? 0}
                      shown={docs.paperworkNotices?.length ?? 0}
                    >
                      <AgencyDocList
                        docs={docs.paperworkNotices ?? []}
                        today={today}
                        showDeadline
                      />
                    </PaperworkFold>
                    {docs.openCommentsCount >
                      docs.openComments.length + (docs.paperworkNotices?.length ?? 0) && (
                      <Link href={`${agencyDocumentsHref(a.slug)}&comment=open`} className={MORE}>
                        [All {docs.openCommentsCount} open] →
                      </Link>
                    )}
                  </>
                ) : (
                  <p className="mt-2 text-sm text-ink-lo">
                    {docs.linking
                      ? `None linked to this agency yet. ${LINKING_TAIL}`
                      : "No comment period of this agency is open on Civitas right now."}
                  </p>
                )}
              </section>

              <section aria-labelledby="proposed-heading">
                <h2 id="proposed-heading" className={H2}>
                  Proposed rules
                </h2>
                {docs.proposedRules.length > 0 ? (
                  <AgencyDocList docs={docs.proposedRules} today={today} />
                ) : (
                  <p className="mt-2 text-sm text-ink-lo">
                    {docs.linking
                      ? `None linked to this agency yet. ${LINKING_TAIL}`
                      : "None on Civitas."}
                  </p>
                )}
              </section>

              <section aria-labelledby="final-heading">
                <h2 id="final-heading" className={H2}>
                  Final rules
                </h2>
                {docs.finalRules.length > 0 ? (
                  <AgencyDocList docs={docs.finalRules} today={today} />
                ) : (
                  <p className="mt-2 text-sm text-ink-lo">
                    {docs.linking
                      ? `None linked to this agency yet. ${LINKING_TAIL}`
                      : "None on Civitas."}
                  </p>
                )}
              </section>

              {a.actionIssues.length > 0 && (
                <section aria-labelledby="issues-heading">
                  <h2 id="issues-heading" className={H2}>
                    In the Action Center
                  </h2>
                  <p className="mt-1 text-xs text-ink-min">
                    Issues that cite one of its documents, newest first
                  </p>
                  <ul className="divide-y divide-white/[0.07]">
                    {a.actionIssues.map((i) => (
                      <li key={i.id} className="py-3">
                        <p className="font-mono text-xs text-ink-min">{readOn(i.date)}</p>
                        <Link
                          href={`/issue/${i.id}`}
                          className="mt-1 block text-sm leading-snug text-ink-hi hover:text-phos"
                        >
                          {i.title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section aria-labelledby="eo-heading">
                <h2 id="eo-heading" className={H2}>
                  Executive orders naming it
                </h2>
                {eos === null ? (
                  <p className="mt-2 text-sm text-ink-lo">
                    The search index couldn&apos;t be asked just now.
                  </p>
                ) : eos.items.length > 0 ? (
                  <>
                    <p className="mt-1 text-xs text-ink-min">
                      {eos.count}
                      {eos.capped ? "+" : ""} order{eos.count === 1 ? "" : "s"} whose text says
                      &ldquo;
                      {eos.names.join("” or “")}&rdquo;, newest first
                    </p>
                    <AgencyDocList docs={eos.items} today={today} />
                  </>
                ) : (
                  <p className="mt-2 text-sm text-ink-lo">
                    No executive order on Civitas names it.
                  </p>
                )}
              </section>
            </div>

            {docs.total > 0 && (
              <Link href={agencyDocumentsHref(a.slug)} className={`${MORE} mt-8 text-sm`}>
                [Search all {docs.total.toLocaleString("en-US")} of its documents in Explore] →
              </Link>
            )}

            {(a.parent || a.children.length > 0) && (
              <section aria-labelledby="org-heading" className="mt-10">
                <h2 id="org-heading" className={H2}>
                  {a.children.length > 0 ? "Sub-agencies" : "Part of"}
                </h2>
                {a.parent && (
                  <p className="mt-2 text-sm text-ink">
                    Part of the{" "}
                    <Link
                      href={agencyHref(a.parent.slug)}
                      className="text-signal-cyan underline underline-offset-2 hover:text-phos"
                    >
                      {a.parent.name}
                    </Link>
                    .
                  </p>
                )}
                {a.children.length > 0 && (
                  <ul className="mt-2 grid grid-cols-1 gap-x-6 sm:grid-cols-2">
                    {a.children.map((c) => (
                      <li
                        key={c.slug}
                        className="flex items-baseline justify-between gap-3 border-b border-white/[0.07] py-2"
                      >
                        <Link
                          href={agencyHref(c.slug)}
                          className="text-sm text-ink-hi hover:text-phos"
                        >
                          {c.name}
                        </Link>
                        <span className="shrink-0 font-mono text-xs tabular-nums text-ink-min">
                          {c.documents > 0 ? c.documents.toLocaleString("en-US") : "none"}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </div>
        </main>
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}
