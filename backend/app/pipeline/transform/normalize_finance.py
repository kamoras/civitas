"""Normalize FEC financial data into the Senator funding shape.

This is the most complex transform module. It handles:
- PAC vs individual contribution separation
- Donor type classification (PAC, Party/Ideological, Org/Employees)
- Candidate self-contribution filtering
- Industry breakdown via the static classifier
"""

import logging

from app.pipeline.transform.industry_classifier import classify_industry

logger = logging.getLogger(__name__)

# Words that indicate self-contributions, payment processors, or inter-committee transfers
SKIP_PAC_PATTERNS = [
    "WINRED",
    "ACTBLUE",
    "ANEDOT",  # payment processors, not actual donors
    "VICTORY COMMITTEE",
    "VICTORY FUND",
    "JOINT FUNDRAISING",
    "INFORMATION REQUESTED",
]

# Generic party/ideological PACs -- not specific corporate interests.
# These get tagged as "Party/Ideological" rather than skipped.
GENERIC_PARTY_PAC_PATTERNS = [
    # National party committees
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
    # State party committees
    "STATE DEMOCRATIC",
    "STATE REPUBLICAN",
    "DEMOCRATIC PARTY OF",
    "REPUBLICAN PARTY OF",
    # Super PACs and ideological fundraising
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
    # Leadership PACs
    "LEADERSHIP PAC",
    "LEADERSHIP FUND",
    "PAC FOR AMERICA",
    "RECLAIM AMERICA",
]

# Employers to skip when grouping individual contributions
SKIP_EMPLOYERS = {
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
    "INFO REQUESTED",
}


def normalize_finance(
    candidate: dict | None,
    financials: list[dict],
    individual_receipts: list[dict],
    pac_receipts: list[dict],
    aggregated_contributors: list[dict],
) -> dict:
    """Normalize FEC financial data into the Senator funding shape.

    Args:
        candidate: FEC candidate record.
        financials: FEC financial totals (by cycle).
        individual_receipts: Individual contribution receipts (Schedule A, is_individual=true).
        pac_receipts: PAC/committee contribution receipts (Schedule A, is_individual=false).
        aggregated_contributors: Top contributors by total.

    Returns:
        Normalized funding object matching Senator.funding type.
    """
    # Sum across recent election cycles (most recent 2)
    recent_cycles = financials[:2]

    total_raised = sum(c.get("receipts", 0) or 0 for c in recent_cycles)
    total_from_pacs = sum(
        c.get("other_political_committee_contributions", 0) or 0
        for c in recent_cycles
    )
    small_individual = sum(
        c.get("individual_unitemized_contributions", 0) or 0
        for c in recent_cycles
    )

    small_donor_percentage = (
        round((small_individual / total_raised) * 100) if total_raised > 0 else 0
    )

    # Build top donors: PACs first, then employer-grouped individuals
    candidate_name = (candidate or {}).get("name", "")
    top_donors = build_top_donors(
        pac_receipts, individual_receipts, aggregated_contributors, candidate_name
    )

    # Build industry breakdown from all receipts
    all_receipts = individual_receipts + pac_receipts
    industry_breakdown = _build_industry_breakdown(all_receipts, total_raised)

    return {
        "totalRaised": round(total_raised),
        "totalFromPACs": round(total_from_pacs),
        "smallDonorPercentage": small_donor_percentage,
        "topDonors": top_donors,
        "industryBreakdown": industry_breakdown,
    }


def build_top_donors(
    pac_receipts: list[dict],
    individual_receipts: list[dict],
    aggregated_contributors: list[dict],
    candidate_name: str,
) -> list[dict]:
    """Build top donors list prioritizing PAC/corporate money.

    PAC contributions show up directly by committee name.
    Individual contributions are grouped by employer to show corporate influence.
    """
    donor_map: dict[str, dict] = {}

    # 1. Process PAC/committee contributions -- these are the direct corporate money
    for r in pac_receipts:
        name = r.get("contributor_name") or ""
        if not name:
            committee = r.get("committee") or {}
            name = committee.get("name", "")
        if not name or name == "Unknown":
            continue

        name_upper = name.upper().strip()

        # Skip payment processors, victory funds, and self-transfers
        if any(p in name_upper for p in SKIP_PAC_PATTERNS):
            continue

        # Skip self-contributions (candidate's own name in contributor)
        if candidate_name:
            candidate_last_name = candidate_name.split(",")[0].split()[0].upper()
            if (
                len(candidate_last_name) > 2
                and candidate_last_name in name_upper
                and (
                    "FOR " in name_upper
                    or ", " in name_upper
                    or name_upper == candidate_name.upper()
                )
            ):
                continue

        # Skip inter-committee transfers
        memo_text = (r.get("memo_text") or "").upper()
        if (
            "TRANSFER" in memo_text
            or "REDESIGNATION" in memo_text
            or "REATTRIBUTION" in memo_text
        ):
            continue

        is_generic_party = any(p in name_upper for p in GENERIC_PARTY_PAC_PATTERNS)
        donor_type = "Party/Ideological" if is_generic_party else "PAC"

        existing = donor_map.get(name_upper, {
            "name": name,
            "total": 0,
            "type": donor_type,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        donor_map[name_upper] = existing

    # 2. Process individual contributions -- group by employer to show corporate ties
    for r in individual_receipts:
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in SKIP_EMPLOYERS:
            continue

        existing = donor_map.get(employer, {
            "name": r.get("contributor_employer", ""),
            "total": 0,
            "type": "Org/Employees",
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        # Don't overwrite PAC type if we already have PAC entries for this org
        if existing["type"] != "PAC":
            existing["type"] = "Org/Employees"
        donor_map[employer] = existing

    # 3. Include aggregated contributors as fallback
    for c in aggregated_contributors:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue

        normalized_name = name.upper().strip()
        if normalized_name not in donor_map:
            donor_map[normalized_name] = {
                "name": name,
                "total": c.get("total", 0) or 0,
                "type": "PAC" if c.get("committee_id") else "Org/Employees",
            }

    # Sort by total, take top 10
    sorted_donors = sorted(donor_map.values(), key=lambda d: d["total"], reverse=True)
    return [
        {
            "name": _clean_donor_name(d["name"]),
            "total": round(d["total"]),
            "type": d["type"],
        }
        for d in sorted_donors[:10]
    ]


def _clean_donor_name(name: str) -> str:
    """Convert FEC ALL CAPS names to title case, preserving acronyms."""
    if name == name.upper():
        acronyms = {"llc", "inc", "pac", "corp", "co", "ltd", "lp", "pllc"}
        words = name.lower().split()
        return " ".join(
            word.upper() if word in acronyms else word[0].upper() + word[1:]
            if word else word
            for word in words
        )
    return name


def _build_industry_breakdown(
    receipts: list[dict], total_raised: float
) -> list[dict]:
    """Group contributions by employer/organization and classify into industries."""
    industry_totals: dict[str, dict] = {}

    for r in receipts:
        org = (
            r.get("contributor_employer")
            or r.get("contributor_organization_name")
            or r.get("contributor_name")
            or ""
        )
        if not org:
            continue

        industry = classify_industry(org)
        existing = industry_totals.get(industry, {
            "industry": industry,
            "name": industry,
            "total": 0,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        industry_totals[industry] = existing

    # Convert to list, calculate percentages, sort
    breakdown = []
    for ind in industry_totals.values():
        total = round(ind["total"])
        percentage = round((ind["total"] / total_raised) * 100) if total_raised > 0 else 0
        if total > 0 and percentage >= 1:
            breakdown.append({
                "industry": ind["industry"],
                "name": ind["industry"].replace("_", " "),
                "total": total,
                "percentage": percentage,
            })

    breakdown.sort(key=lambda x: x["total"], reverse=True)
    return breakdown[:8]  # Top 8 industries
