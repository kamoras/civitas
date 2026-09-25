// Funding shares (PAC %, small-donor %) are taken over contributions —
// money from contributors plus the candidate's own — not total receipts,
// which also count transfers in from joint fundraising committees and
// loans. The backend computes smallDonorPercentage on that base
// (normalize_finance.summarize_election_totals); every PAC share shown
// must use the same one, or the two percentages on one card describe
// different totals. Records scored before the field existed have no
// totalContributions and fall back to totalRaised.

interface FundingTotals {
  totalRaised?: number | null;
  totalContributions?: number | null;
}

export function fundingShareBase(f: FundingTotals): number {
  return f.totalContributions || f.totalRaised || 0;
}

/** PAC share of contributions, 0-100 (unrounded); 0 when nothing was raised. */
export function pacSharePct(pacTotal: number | null | undefined, f: FundingTotals): number {
  const base = fundingShareBase(f);
  return base > 0 ? ((pacTotal ?? 0) / base) * 100 : 0;
}
