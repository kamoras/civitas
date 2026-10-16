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

export interface AgencyYearFigures {
  fiscalYear: number;
  budgetaryResources: number | null;
  obligated: number | null;
  outlays: number | null;
  throughPeriod: number | null;
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
  /** False when USAspending hasn't reported the current fiscal year: the
   * year's figures are then null, never 0, and `latest` is the newest
   * year it has. */
  reported: boolean;
  latest: AgencyYearFigures | null;
  /** The fiscal month (1 = October) the year's figures run through. */
  throughPeriod: number | null;
  /** USAspending's budget_authority_amount: the year's total budgetary
   * resources, as its agency pages label it. */
  budgetAuthority: number | null;
  obligated: number | null;
  outlays: number | null;
}

/** Documents stored before agencies were read, still being linked to the
 * agency that issued them; null once every one is. While it is set, a
 * document count is a count so far, never "none". */
export type DocumentsLinking = { linked: number; total: number } | null;

export interface AgencyIndex {
  agencies: AgencySummary[];
  /** Optional only for a response cached before the field. */
  documentsLinking?: DocumentsLinking;
  agenciesSource: AgencySource;
  agenciesAsOf: string | null;
  budget: {
    fiscalYear: number | null;
    fiscalQuarter: number | null;
    throughPeriod: number | null;
    /** Over the agencies that reported the year. */
    total: number;
    reportedCount: number;
    agencies: (AgencyBudgetRow & { share: number | null; barPercent: number })[];
    unmatched: string[];
    source: AgencySource;
    asOf: string | null;
  } | null;
  documentsSource: AgencySource;
  documentsAsOf: string | null;
  /** "not_read": USAspending hasn't been read (an outage or a new install). */
  budgetStatus: "read" | "not_read";
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
        retiredAt: string | null;
        source: AgencySource;
        asOf: string | null;
      })
    | null;
  /** For a sub-agency: the department whose budget includes its spending. */
  budgetOf: { slug: string; name: string } | null;
  budgetStatus:
    "reported" | "not_yet_reported" | "none" | "retired" | "part_of" | "unmatched" | "not_read";
  /** For an agency with no budget of its own: USAspending's agencies no
   * Register agency matched. */
  unmatchedBudgets: string[];
  /** Action Center issues citing one of its documents, newest first. */
  actionIssues: { id: string; title: string; date: string; documents: number }[];
  documents: {
    total: number;
    byType: Record<string, number>;
    openComments: AgencyDocument[];
    openCommentsCount: number;
    proposedRules: AgencyDocument[];
    finalRules: AgencyDocument[];
    /** Optional only for a response cached before the field. */
    linking?: DocumentsLinking;
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
