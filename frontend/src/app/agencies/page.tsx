import Link from "next/link";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeAgencyIndex } from "@/lib/seo";
import { fetchAgencyIndex } from "@/lib/agencyServer";
import { fiscalYearLabel } from "@/lib/agencies";
import { formatCurrency } from "@/lib/formatting";
import { agencyHref } from "@/lib/routes";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import BudgetBars from "@/components/agencies/BudgetBars";
import SourceLine from "@/components/agencies/SourceLine";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";
import type { AgencySummary } from "@/types/agency";

const { title, description } = describeAgencyIndex();
export const metadata = pageMetadata({ title, description, path: "/agencies" });

// The bars shown before "every other agency": a phone screen's worth.
const BARS_SHOWN = 12;
const RESULTS = "#agency-directory";

const FIELD =
  "min-h-11 w-full border border-white/15 bg-surface-base px-3 font-mono text-sm text-ink-hi outline-none focus:border-phos/40";

function one(v: string | string[] | undefined): string {
  return (Array.isArray(v) ? v[0] : v)?.trim() ?? "";
}

function AgencyRow({ a, parentName }: { a: AgencySummary; parentName?: string }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5">
      <span className="min-w-0">
        <Link href={agencyHref(a.slug)} className="text-sm text-ink-hi hover:text-phos">
          {a.name}
        </Link>
        {a.shortName && a.shortName !== a.name && (
          <span className="ml-2 font-mono text-xs text-ink-min">{a.shortName}</span>
        )}
        {parentName && <span className="block text-xs text-ink-min">Part of {parentName}</span>}
      </span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-ink-lo">
        {a.documents > 0
          ? `${a.documents.toLocaleString("en-US")} document${a.documents === 1 ? "" : "s"}`
          : "no documents yet"}
        {a.children > 0 && ` · ${a.children} sub-agenc${a.children === 1 ? "y" : "ies"}`}
      </span>
    </li>
  );
}

/**
 * The executive branch's agencies: where the budget goes, as a graphic, and
 * every agency the Federal Register lists, findable by name. A plain GET
 * form over the server's render, so a search is a URL and works without
 * JavaScript.
 */
export default async function AgenciesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const q = one((await searchParams).q).toLowerCase();
  const index = await fetchAgencyIndex();
  const budget = index.budget;
  const byslug = new Map(index.agencies.map((a) => [a.slug, a]));
  const listed = q
    ? index.agencies.filter(
        (a) => a.name.toLowerCase().includes(q) || (a.shortName ?? "").toLowerCase() === q
      )
    : index.agencies.filter((a) => !a.parentSlug || !byslug.has(a.parentSlug));
  // Agencies with something on Civitas first, then the rest by name.
  const withRecords = listed.filter((a) => a.documents > 0 || a.budgetAuthority != null);
  const without = listed.filter((a) => !(a.documents > 0 || a.budgetAuthority != null));
  const fy = budget ? fiscalYearLabel(budget.fiscalYear, budget.throughPeriod) : "";
  const shown = budget?.agencies.slice(0, BARS_SHOWN) ?? [];
  const rest = budget?.agencies.slice(BARS_SHOWN) ?? [];

  return (
    <ShareSubjectProvider
      subject={{
        title: "Federal agencies",
        subtitle: fy ? `Budgetary resources, ${fy}` : "",
        url: absoluteUrl("/agencies"),
      }}
    >
      <JsonLd
        data={[
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Agencies", url: absoluteUrl("/agencies") },
          ]),
        ]}
      />
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto max-w-5xl">
            <PageMasthead
              className="mb-8"
              eyebrow="Agencies · the executive branch"
              title="Federal agencies"
            >
              <p>
                The agencies that run the government day to day: what each one spends, the rules it
                proposes and issues, and when you can comment on them. Pick an agency for its page.
              </p>
            </PageMasthead>

            <form method="get" action={`/agencies${RESULTS}`} className="mb-8 flex gap-2">
              <label className="sr-only" htmlFor="agency-q">
                Find an agency by name
              </label>
              <input
                id="agency-q"
                type="search"
                name="q"
                defaultValue={q}
                placeholder="Find an agency: e.g. EPA, Education, Forest Service"
                className={FIELD}
              />
              <button
                type="submit"
                className="min-h-11 shrink-0 border border-phos/40 px-4 font-mono text-sm tracking-[0.12em] text-phos hover:bg-phos/10"
              >
                [ FIND ]
              </button>
            </form>

            {budget && (
              <section
                id="budget"
                data-share-section="budget"
                aria-labelledby="budget-heading"
                className="mb-10 scroll-mt-28 border border-white/[0.07]"
              >
                <TerminalTitlebar title={`Budgetary resources · ${fy}`} />
                <div className="p-4">
                  <div className="flex items-baseline justify-between gap-3">
                    <h2
                      id="budget-heading"
                      className="font-display text-xl font-extrabold text-ink-hi"
                    >
                      Where the money goes
                    </h2>
                    <ShareSectionButton label="Agency budgets" />
                  </div>
                  <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink">
                    Budgetary resources are the money an agency can commit in a fiscal year: new
                    funding from Congress plus what it carried over. {formatCurrency(budget.total)}{" "}
                    across {budget.agencies.length} agencies in {fy}; the {shown.length} largest:
                  </p>
                  <div className="mt-4">
                    <BudgetBars rows={shown} />
                  </div>
                  {rest.length > 0 && (
                    <details className="mt-3" data-share-exclude>
                      <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos">
                        [Every other agency: {rest.length}]
                      </summary>
                      <div className="mt-2">
                        <BudgetBars rows={rest} />
                      </div>
                    </details>
                  )}
                  <SourceLine
                    source={budget.source}
                    asOf={budget.asOf}
                    detail={`toptier agencies, ${fy}`}
                  />
                  {budget.unmatched.length > 0 && (
                    <details className="mt-2" data-share-exclude>
                      <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs text-ink-min hover:text-phos">
                        {budget.unmatched.length} not linked: no Federal Register agency&apos;s name
                        matched
                      </summary>
                      <p className="mt-2 max-w-2xl text-xs leading-relaxed text-ink-lo">
                        USAspending.gov and the Federal Register name agencies differently, so
                        Civitas pairs them by name and leaves a pair it is unsure of unlinked rather
                        than guess: {budget.unmatched.join("; ")}.
                      </p>
                    </details>
                  )}
                </div>
              </section>
            )}

            <section aria-labelledby="agency-directory">
              <h2
                id="agency-directory"
                className="scroll-mt-[var(--header-clearance)] font-display text-xl font-extrabold text-ink-hi"
              >
                Every agency
              </h2>
              <p className="mt-3 font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
                {q
                  ? `${listed.length} matching "${q}"`
                  : `${listed.length} departments and independent agencies`}
                {q && (
                  <>
                    {" · "}
                    <Link href="/agencies" className="underline underline-offset-2 hover:text-phos">
                      Clear
                    </Link>
                  </>
                )}
              </p>
              {index.agencies.length === 0 && (
                <p className="mt-3 text-sm text-ink-lo">
                  The Federal Register&apos;s agency list hasn&apos;t been read yet. It is read
                  weekly; this is a gap on Civitas, not a government without agencies.
                </p>
              )}
              <ul className="mt-2 divide-y divide-white/[0.07] border-t border-white/[0.07]">
                {withRecords.map((a) => (
                  <AgencyRow
                    key={a.slug}
                    a={a}
                    parentName={a.parentSlug ? byslug.get(a.parentSlug)?.name : undefined}
                  />
                ))}
              </ul>
              {without.length > 0 && (
                <details className="mt-4" open={!!q && withRecords.length === 0}>
                  <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos">
                    [{without.length} more with no documents or budget on Civitas, many abolished]
                  </summary>
                  <ul className="mt-2 divide-y divide-white/[0.07]">
                    {without.map((a) => (
                      <AgencyRow
                        key={a.slug}
                        a={a}
                        parentName={a.parentSlug ? byslug.get(a.parentSlug)?.name : undefined}
                      />
                    ))}
                  </ul>
                </details>
              )}
              <SourceLine
                source={index.agenciesSource}
                asOf={index.agenciesAsOf}
                detail="agency list"
              />
              <SourceLine
                source={index.documentsSource}
                asOf={index.documentsAsOf}
                detail="document counts on Civitas"
              />
            </section>
          </div>
        </main>
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}
