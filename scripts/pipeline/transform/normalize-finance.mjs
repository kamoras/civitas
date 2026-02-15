import { log } from "../config.mjs";
import { classifyIndustry } from "./industry-classifier.mjs";

/**
 * Normalize FEC financial data into the Senator funding shape.
 * @param {Object} candidate - FEC candidate record
 * @param {Array} financials - FEC financial totals (by cycle)
 * @param {Array} individualReceipts - Individual contribution receipts (Schedule A, is_individual=true)
 * @param {Array} pacReceipts - PAC/committee contribution receipts (Schedule A, is_individual=false)
 * @param {Array} aggregatedContributors - Top contributors by total
 * @returns {Object} Normalized funding object matching Senator.funding type
 */
export function normalizeFinance(
  candidate,
  financials,
  individualReceipts,
  pacReceipts,
  aggregatedContributors
) {
  // Sum across recent election cycles (most recent 2)
  const recentCycles = financials.slice(0, 2);

  const totalRaised = recentCycles.reduce((sum, c) => sum + (c.receipts || 0), 0);
  const totalFromPACs = recentCycles.reduce(
    (sum, c) => sum + (c.other_political_committee_contributions || 0),
    0
  );
  const smallIndividual = recentCycles.reduce(
    (sum, c) => sum + (c.individual_unitemized_contributions || 0),
    0
  );

  const smallDonorPercentage =
    totalRaised > 0 ? Math.round((smallIndividual / totalRaised) * 100) : 0;

  // Build top donors: PACs first, then employer-grouped individuals
  const candidateName = candidate?.name || "";
  const topDonors = buildTopDonors(
    pacReceipts,
    individualReceipts,
    aggregatedContributors,
    candidateName
  );

  // Build industry breakdown from all receipts
  const allReceipts = [...individualReceipts, ...pacReceipts];
  const industryBreakdown = buildIndustryBreakdown(allReceipts, totalRaised);

  return {
    totalRaised: Math.round(totalRaised),
    totalFromPACs: Math.round(totalFromPACs),
    smallDonorPercentage,
    topDonors,
    industryBreakdown,
  };
}

/**
 * Build top donors list prioritizing PAC/corporate money.
 * PAC contributions show up directly by committee name.
 * Individual contributions are grouped by employer to show corporate influence.
 */
function buildTopDonors(pacReceipts, individualReceipts, aggregatedContributors, candidateName) {
  const donorMap = new Map();

  // Words that indicate self-contributions, payment processors, or inter-committee transfers
  const SKIP_PAC_PATTERNS = [
    "WINRED",
    "ACTBLUE",
    "ANEDOT", // payment processors, not actual donors
    "VICTORY COMMITTEE",
    "VICTORY FUND",
    "JOINT FUNDRAISING",
    "INFORMATION REQUESTED",
  ];

  // Generic party/ideological PACs — not specific corporate interests.
  // These get tagged as "Party/Ideological" rather than skipped.
  const GENERIC_PARTY_PAC_PATTERNS = [
    // National party committees
    "DEMOCRATIC NATIONAL COMMITTEE",
    "REPUBLICAN NATIONAL COMMITTEE",
    "DEMOCRATIC SENATORIAL CAMPAIGN",
    "DSCC",
    "NATIONAL REPUBLICAN SENATORIAL",
    "NRSC",
    "DEMOCRATIC CONGRESSIONAL CAMPAIGN",
    "DCCC",
    "NATIONAL REPUBLICAN CONGRESSIONAL",
    "NRCC",
    // State party committees
    "STATE DEMOCRATIC",
    "STATE REPUBLICAN",
    "DEMOCRATIC PARTY OF",
    "REPUBLICAN PARTY OF",
    // Super PACs and ideological fundraising
    "EMILY'S LIST",
    "EMILYS LIST",
    "CLUB FOR GROWTH",
    "MOVEON",
    "PRIORITIES USA",
    "SENATE MAJORITY PAC",
    "SENATE LEADERSHIP FUND",
    "HOUSE MAJORITY PAC",
    "CONGRESSIONAL LEADERSHIP FUND",
    "AMERICAN CROSSROADS",
    "END CITIZENS UNITED",
    // Leadership PACs
    "LEADERSHIP PAC",
    "LEADERSHIP FUND",
    "PAC FOR AMERICA",
    "RECLAIM AMERICA",
  ];

  // 1. Process PAC/committee contributions — these are the direct corporate money
  for (const r of pacReceipts) {
    const name = r.contributor_name || r.committee?.name || "Unknown";
    if (!name || name === "Unknown") continue;

    const nameUpper = name.toUpperCase().trim();

    // Skip payment processors, victory funds, and self-transfers
    if (SKIP_PAC_PATTERNS.some((p) => nameUpper.includes(p))) continue;
    // Skip self-contributions (candidate's own name in contributor)
    if (candidateName) {
      const candidateLastName = candidateName.split(/[,\s]+/)[0].toUpperCase();
      if (
        candidateLastName.length > 2 &&
        nameUpper.includes(candidateLastName) &&
        (nameUpper.includes("FOR ") ||
          nameUpper.includes(", ") ||
          nameUpper === candidateName.toUpperCase())
      )
        continue;
    }
    // Skip inter-committee transfers
    if (
      r.memo_text?.toUpperCase()?.includes("TRANSFER") ||
      r.memo_text?.toUpperCase()?.includes("REDESIGNATION") ||
      r.memo_text?.toUpperCase()?.includes("REATTRIBUTION")
    )
      continue;

    const isGenericParty = GENERIC_PARTY_PAC_PATTERNS.some((p) => nameUpper.includes(p));
    const donorType = isGenericParty ? "Party/Ideological" : "PAC";

    const existing = donorMap.get(nameUpper) || {
      name,
      total: 0,
      type: donorType,
    };
    existing.total += r.contribution_receipt_amount || 0;
    donorMap.set(nameUpper, existing);
  }

  // 2. Process individual contributions — group by employer to show corporate ties
  const SKIP_EMPLOYERS = new Set([
    "NONE",
    "N/A",
    "SELF-EMPLOYED",
    "SELF EMPLOYED",
    "RETIRED",
    "NOT EMPLOYED",
    "SELF",
    "HOMEMAKER",
    "INFORMATION REQUESTED",
    "STUDENT",
    "UNEMPLOYED",
    "DISABLED",
    "NOT APPLICABLE",
    "REQUESTED",
    "INFORMATION REQUESTED PER BEST EFFORTS",
    "INFORMATION REQUESTED PER BEST EFFO",
    "INFORMATION REQUESTED",
    "INFO REQUESTED",
  ]);

  for (const r of individualReceipts) {
    const employer = (r.contributor_employer || "").toUpperCase().trim();
    if (!employer || SKIP_EMPLOYERS.has(employer)) continue;

    const existing = donorMap.get(employer) || {
      name: r.contributor_employer,
      total: 0,
      type: "Org/Employees",
    };
    existing.total += r.contribution_receipt_amount || 0;
    // Don't overwrite PAC type if we already have PAC entries for this org
    if (existing.type !== "PAC") existing.type = "Org/Employees";
    donorMap.set(employer, existing);
  }

  // 3. Include aggregated contributors as fallback
  for (const c of aggregatedContributors) {
    const name = c.contributor_name || "Unknown";
    if (!name || name === "Unknown") continue;

    const normalizedName = name.toUpperCase().trim();
    if (!donorMap.has(normalizedName)) {
      donorMap.set(normalizedName, {
        name,
        total: c.total || 0,
        type: c.committee_id ? "PAC" : "Org/Employees",
      });
    }
  }

  // Sort by total, take top 10
  return Array.from(donorMap.values())
    .sort((a, b) => b.total - a.total)
    .slice(0, 10)
    .map((d) => ({
      name: cleanDonorName(d.name),
      total: Math.round(d.total),
      type: d.type,
    }));
}

function cleanDonorName(name) {
  // FEC uses ALL CAPS — convert to title case
  if (name === name.toUpperCase()) {
    return name
      .toLowerCase()
      .split(/\s+/)
      .map((word) => {
        if (["llc", "inc", "pac", "corp", "co", "ltd", "lp", "pllc"].includes(word)) {
          return word.toUpperCase();
        }
        return word.charAt(0).toUpperCase() + word.slice(1);
      })
      .join(" ");
  }
  return name;
}

function buildIndustryBreakdown(receipts, totalRaised) {
  // Group contributions by employer/organization and classify
  const industryTotals = new Map();

  for (const r of receipts) {
    const org =
      r.contributor_employer || r.contributor_organization_name || r.contributor_name || "";
    if (!org) continue;

    const industry = classifyIndustry(org);
    const existing = industryTotals.get(industry) || {
      industry,
      name: industry,
      total: 0,
    };
    existing.total += r.contribution_receipt_amount || 0;
    industryTotals.set(industry, existing);
  }

  // Convert to array, calculate percentages, sort
  const breakdown = Array.from(industryTotals.values())
    .map((ind) => ({
      industry: ind.industry,
      name: ind.industry.replace(/_/g, " "),
      total: Math.round(ind.total),
      percentage: totalRaised > 0 ? Math.round((ind.total / totalRaised) * 100) : 0,
    }))
    .filter((ind) => ind.total > 0 && ind.percentage >= 1)
    .sort((a, b) => b.total - a.total)
    .slice(0, 8); // Top 8 industries

  return breakdown;
}
