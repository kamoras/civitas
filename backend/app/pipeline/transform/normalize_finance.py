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

# Words that indicate payment processors or pure pass-through transfers — skip entirely
SKIP_PAC_PATTERNS = [
    "WINRED",
    "ACTBLUE",
    "ANEDOT",  # payment processors, not actual donors
    "VICTORY COMMITTEE",
    "VICTORY FUND",
    "JOINT FUNDRAISING",
    "INFORMATION REQUESTED",
]

# Prefixes/keywords that, when combined with the candidate's own last name,
# indicate the PAC is controlled by or affiliated with the candidate themselves.
# These PACs are tagged "CandidateAffiliated" rather than dropped, so they still
# appear in the donor list (transparent) but are excluded from lobbying-match analysis.
SELF_PAC_PATTERNS = [
    "TEAM ",
    "FRIENDS OF",
    "COMMITTEE FOR ",
    "CITIZENS FOR ",
    "VOLUNTEERS FOR ",
    "PEOPLE FOR ",
    "FOR SENATE",
    "FOR CONGRESS",
    "FOR PRESIDENT",
    "FOR GOVERNOR",
    "LEADERSHIP PAC",
    "LEADERSHIP FUND",
    "FOR AMERICA",
    "FOR FLORIDA",
    "FOR TEXAS",
    "FOR CALIFORNIA",
    "FOR NEW YORK",
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


def _is_candidate_affiliated(name_upper: str, candidate_name: str) -> bool:
    """Return True if a PAC name suggests it is controlled by or affiliated with the candidate.

    FEC candidate names are stored as 'LAST, FIRST M'.  We extract the last name
    and check whether it appears in the PAC name together with any of the strong
    candidate-committee signal words (TEAM, FRIENDS OF, FOR SENATE, etc.).
    """
    if not candidate_name:
        return False

    # Parse last name from "LAST, FIRST" FEC format
    last_name = candidate_name.split(",")[0].strip().upper()
    if len(last_name) <= 2 or last_name not in name_upper:
        return False

    # Candidate's last name is present — now check for affiliated signal words
    return any(p in name_upper for p in SELF_PAC_PATTERNS)


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
    large_individual = sum(
        c.get("individual_itemized_contributions", 0) or 0
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

    # Build industry breakdown: individuals get explicit buckets, PACs get industry-classified
    industry_breakdown = _build_industry_breakdown(
        pac_receipts=pac_receipts,
        small_individual_total=small_individual,
        large_individual_total=large_individual,
        total_raised=total_raised,
    )

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

        # Skip inter-committee transfers
        memo_text = (r.get("memo_text") or "").upper()
        if (
            "TRANSFER" in memo_text
            or "REDESIGNATION" in memo_text
            or "REATTRIBUTION" in memo_text
        ):
            continue

        # Classify donor type — candidate's own committees get a distinct label so
        # they remain visible in the UI but are excluded from lobbying-match analysis.
        if _is_candidate_affiliated(name_upper, candidate_name):
            donor_type = "CandidateAffiliated"
        elif any(p in name_upper for p in GENERIC_PARTY_PAC_PATTERNS):
            donor_type = "Party/Ideological"
        else:
            donor_type = "PAC"

        # Classify industry for this donor
        industry = classify_industry(name)

        existing = donor_map.get(name_upper, {
            "name": name,
            "total": 0,
            "type": donor_type,
            "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        donor_map[name_upper] = existing

    # 2. Process individual contributions -- group by employer to show corporate ties
    for r in individual_receipts:
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in SKIP_EMPLOYERS:
            continue

        # Classify industry for employer-based donations
        industry = classify_industry(r.get("contributor_employer", ""))

        existing = donor_map.get(employer, {
            "name": r.get("contributor_employer", ""),
            "total": 0,
            "type": "Org/Employees",
            "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        # Don't overwrite PAC type if we already have PAC entries for this org
        if existing["type"] != "PAC":
            existing["type"] = "Org/Employees"
        # Keep the industry classification (may already be set if PAC exists)
        if "industry" not in existing:
            existing["industry"] = industry
        donor_map[employer] = existing

    # 3. Include aggregated contributors as fallback
    for c in aggregated_contributors:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue

        normalized_name = name.upper().strip()
        if normalized_name not in donor_map:
            industry = classify_industry(name)
            donor_map[normalized_name] = {
                "name": name,
                "total": c.get("total", 0) or 0,
                "type": "PAC" if c.get("committee_id") else "Org/Employees",
                "industry": industry,
            }

    # Sort by total, take top 10
    sorted_donors = sorted(donor_map.values(), key=lambda d: d["total"], reverse=True)
    return [
        {
            "name": _clean_donor_name(d["name"]),
            "total": round(d["total"]),
            "type": d["type"],
            "industry": d.get("industry", "OTHER"),
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
    pac_receipts: list[dict],
    small_individual_total: float,
    large_individual_total: float,
    total_raised: float,
) -> list[dict]:
    """Build a funding breakdown separating individual donors from corporate/PAC sectors.

    Individual donor money (small and large) gets its own explicit buckets so it
    is never conflated with industry-classified corporate PAC money. Only PAC and
    committee receipts are classified by industry.
    """
    industry_totals: dict[str, dict] = {}

    # Explicit buckets for individual donors (never classified by industry)
    if small_individual_total > 0:
        industry_totals["SMALL_DONORS"] = {
            "industry": "SMALL_DONORS",
            "name": "SMALL_DONORS",
            "total": small_individual_total,
        }
    if large_individual_total > 0:
        industry_totals["LARGE_INDIVIDUAL"] = {
            "industry": "LARGE_INDIVIDUAL",
            "name": "LARGE_INDIVIDUAL",
            "total": large_individual_total,
        }

    # Classify PAC/committee receipts by industry
    for r in pac_receipts:
        org = r.get("contributor_name") or r.get("contributor_organization_name") or ""
        if not org:
            continue
        org_upper = org.upper().strip()
        # Skip payment processors (ActBlue, WinRed, etc.) — they're pass-throughs
        if any(p in org_upper for p in SKIP_PAC_PATTERNS):
            continue

        amount = r.get("contribution_receipt_amount", 0) or 0
        industry = classify_industry(org)
        existing = industry_totals.get(industry, {
            "industry": industry,
            "name": industry,
            "total": 0,
        })
        existing["total"] += amount
        industry_totals[industry] = existing

    # Convert to list with percentages, drop zero entries
    breakdown = []
    for ind in industry_totals.values():
        total = round(ind["total"])
        percentage = round((ind["total"] / total_raised) * 100) if total_raised > 0 else 0
        if total > 0:
            breakdown.append({
                "industry": ind["industry"],
                "name": ind["industry"].replace("_", " "),
                "total": total,
                "percentage": percentage,
            })

    breakdown.sort(key=lambda x: x["total"], reverse=True)
    return breakdown[:12]  # Top 12 buckets
