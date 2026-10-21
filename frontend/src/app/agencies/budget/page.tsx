import { connection } from "next/server";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { describeAgencyBudgets } from "@/lib/seo";
import { fetchAgencyIndex } from "@/lib/agencyServer";
import { fiscalYearLabel } from "@/lib/agencies";
import { formatCurrency } from "@/lib/formatting";
import { AGENCY_BUDGETS_HREF, EXECUTIVE_HREF } from "@/lib/routes";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import SectionTabs from "@/components/layout/SectionTabs";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import BudgetBars from "@/components/agencies/BudgetBars";
import SourceLine from "@/components/agencies/SourceLine";
import ShareSectionButton from "@/components/share/ShareSectionButton";
import { shareMetaAttrs } from "@/lib/shareImage";
import { ShareSubjectProvider } from "@/components/share/ShareSubjectContext";
import JsonLd, { breadcrumbList } from "@/components/seo/JsonLd";

const { title, description } = describeAgencyBudgets();
export const metadata = pageMetadata({ title, description, path: AGENCY_BUDGETS_HREF });

// The bars shown before "every other agency": a phone screen's worth.
const BARS_SHOWN = 12;

/**
 * Every agency's budgetary resources this fiscal year, largest first, each
 * bar linked to its agency's page where the names matched. Its own page,
 * so the Executive section's "Budgets" tab is never "Every agency" again.
 */
export default async function AgencyBudgetsPage() {
  // Rendered per request: `next build` can't reach the backend.
  await connection();
  const index = await fetchAgencyIndex();
  const budget = index.budget;
  const fy = budget ? fiscalYearLabel(budget.fiscalYear, budget.throughPeriod) : "";
  // Reported agencies come first (the backend's order); one not yet
  // reported is never a bar.
  const shown = budget?.agencies.filter((r) => r.reported).slice(0, BARS_SHOWN) ?? [];
  const rest = budget?.agencies.filter((r) => !shown.includes(r)) ?? [];
  const unreported = budget ? budget.agencies.length - budget.reportedCount : 0;

  return (
    <ShareSubjectProvider
      subject={{
        title: "Federal agency budgets",
        subtitle: fy ? `Budgetary resources, ${fy}` : "",
        url: absoluteUrl(AGENCY_BUDGETS_HREF),
        source: "USAspending.gov",
        asOf: budget?.asOf ?? null,
      }}
    >
      <JsonLd
        data={[
          breadcrumbList([
            { name: "Home", url: absoluteUrl("/") },
            { name: "Executive", url: absoluteUrl(EXECUTIVE_HREF) },
            { name: "Agencies", url: absoluteUrl("/agencies") },
            { name: "Budgets", url: absoluteUrl(AGENCY_BUDGETS_HREF) },
          ]),
        ]}
      />
      <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
        <Navbar />
        <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
          <div className="mx-auto max-w-5xl">
            <SectionTabs section="executive" active="budget" className="mb-6" />
            <PageMasthead
              className="mb-8"
              eyebrow="Executive · budgets"
              title="Federal agency budgets"
            >
              <p>
                Where the money goes: what each agency can spend this fiscal year, largest first.
                Pick an agency for its page.
              </p>
            </PageMasthead>

            {!budget ? (
              <p className="text-sm text-ink-lo">
                USAspending.gov&apos;s budgets haven&apos;t been read yet, so no spending is shown.
                That is a gap on Civitas, not a government without a budget.
              </p>
            ) : (
              <section
                id="budget"
                data-share-section="budget"
                {...shareMetaAttrs({ source: "USAspending.gov", asOf: budget.asOf })}
                aria-labelledby="budget-heading"
                className="border border-white/[0.07]"
              >
                <TerminalTitlebar title={`Budgetary resources · ${fy}`} />
                <div className="p-4">
                  <div className="flex items-baseline justify-between gap-3">
                    <h2
                      id="budget-heading"
                      className="font-display text-xl font-semibold text-ink-hi"
                    >
                      Where the money goes
                    </h2>
                    <ShareSectionButton label="Agency budgets" />
                  </div>
                  <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink">
                    Budgetary resources are the money an agency can commit in a fiscal year: new
                    funding from Congress plus what it carried over. {formatCurrency(budget.total)}{" "}
                    across the {budget.reportedCount} agencies that have reported {fy}
                    {unreported > 0
                      ? ` (${unreported} more haven't reported FY${budget.fiscalYear} yet)`
                      : ""}
                    ; the {shown.length} largest:
                  </p>
                  <div className="mt-4">
                    <BudgetBars rows={shown} />
                  </div>
                  {rest.length > 0 && (
                    <details className="mt-3" data-share-exclude>
                      <summary className="inline-flex min-h-6 cursor-pointer items-center font-mono text-xs text-ink-lo hover:text-phos">
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
          </div>
        </main>
        <Footer />
      </div>
    </ShareSubjectProvider>
  );
}
