/** Where a block of an agency page comes from (backend api/agencies.py). */
export interface AgencySource {
  name: string;
  url: string;
}

export interface AgencySummary {
  slug: string;
  name: string;
  shortName: string | null;
  parentSlug: string | null;
  children: number;
  /** Federal Register documents Civitas holds from it and its sub-agencies. */
  documents: number;
  budgetAuthority: number | null;
}

export interface AgencyBudgetRow {
  toptierCode: string;
  /** USAspending's name for it. */
  name: string;
  abbreviation: string | null;
  /** The Federal Register agency its name matched; null when none did. */
  slug: string | null;
  fiscalYear: number | null;
  fiscalQuarter: number | null;
  /** The fiscal month (1 = October) the year's figures run through. */
  throughPeriod: number | null;
  /** USAspending's budget_authority_amount: the year's total budgetary
   * resources, as its agency pages label it. */
  budgetAuthority: number | null;
  obligated: number | null;
  outlays: number | null;
}

export interface AgencyIndex {
  agencies: AgencySummary[];
  agenciesSource: AgencySource;
  agenciesAsOf: string | null;
  budget: {
    fiscalYear: number | null;
    fiscalQuarter: number | null;
    throughPeriod: number | null;
    total: number;
    agencies: (AgencyBudgetRow & { share: number | null; barPercent: number })[];
    unmatched: string[];
    source: AgencySource;
    asOf: string | null;
  } | null;
  documentsSource: AgencySource;
  documentsAsOf: string | null;
}

export interface AgencyDocument {
  id: number;
  title: string;
  docType: string;
  date: string;
  agencyName: string;
  agencySlug: string;
  url: string;
  commentsCloseOn: string;
  /** Only while its comment period is open. */
  commentUrl: string;
}

export interface AgencyBudgetYear {
  fiscalYear: number;
  budgetaryResources: number | null;
  obligated: number | null;
  outlays: number | null;
  /** The fiscal month the year's figures run through (12: the whole year). */
  throughPeriod: number | null;
  barPercent: number;
}

export interface AgencyDetail {
  slug: string;
  name: string;
  shortName: string | null;
  description: string | null;
  frUrl: string | null;
  website: string | null;
  parent: { slug: string; name: string } | null;
  children: { slug: string; name: string; documents: number }[];
  profileSource: AgencySource;
  profileAsOf: string | null;
  budget:
    | (AgencyBudgetRow & {
        matchScore: number | null;
        byYear: AgencyBudgetYear[] | null;
        byYearAsOf: string | null;
        source: AgencySource;
        asOf: string | null;
      })
    | null;
  /** For a sub-agency: the department whose budget includes its spending. */
  budgetOf: { slug: string; name: string } | null;
  documents: {
    total: number;
    byType: Record<string, number>;
    openComments: AgencyDocument[];
    openCommentsCount: number;
    proposedRules: AgencyDocument[];
    finalRules: AgencyDocument[];
  };
  /** Null when the keyword index couldn't be asked (not "none name it"). */
  executiveOrders: {
    count: number;
    capped: boolean;
    names: string[];
    items: AgencyDocument[];
  } | null;
  documentsSource: AgencySource;
  documentsAsOf: string | null;
}
