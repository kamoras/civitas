"""Normalize FEC financial data into the Senator funding shape.

This is the most complex transform module. It handles:
- PAC vs individual contribution separation
- Donor type classification (PAC, Party/Ideological, Org/Employees)
- Candidate self-contribution filtering
- Industry breakdown via AI-based classifier (when provided)
"""

import logging
import re

from app.pipeline.transform.industry_classifier import classify_with_learning

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

# Corporate/organizational suffixes that indicate employee contributions,
# not actual PACs. These should be classified as "Org/Employees".
CORPORATE_ORG_PATTERNS = [
    " BANK",
    " & COMPANY",
    " CORPORATION",
    " CORP",
    " INC",
    " LLC",
    " LLP",
    " LIMITED",
    " PARTNERS",
    " ASSOCIATES",
    " GROUP",
    " HOLDINGS",
    " ENTERPRISES",
    " INDUSTRIES",
    " SERVICES",
    " SYSTEMS",
    " SOLUTIONS",
    " TECHNOLOGIES",
    " PHARMA",
    " PHARMACEUTICALS",
    " ENERGY",
    " OIL",
    " GAS",
    " CAPITAL",
    " INVESTMENT",
    " FINANCIAL",
    " INSURANCE",
    " HEALTHCARE",
    " HOSPITAL",
    " MEDICAL",
    " UNIVERSITY",
    " COLLEGE",
    " LAW FIRM",
    "CREDIT UNION",
    " HEALTH SYSTEM",
    " HEALTH CENTER",
    " CLINIC",
    " DENTAL",
    " PHYSICIANS",
    " NURSES",
    " COOPERATIVE",
    " CO-OP",
]

# Keywords that indicate a political/campaign entity regardless of LLM classification.
# These are NOT industry donors — they're campaign infrastructure.
POLITICAL_OVERRIDE_PATTERNS = [
    "VICTORY FUND", "VICTORY FU",
    "FOR PRESIDENT", "FOR SENATE", "FOR CONGRESS",
    "FOR GOVERNOR", "SENATE VICTORY",
    "JOINT FUNDRAISING", "VICTORY COMMITTEE",
    "ACTION FUND",
    "NGP VAN",
    "DEMOCRATIC NATIONAL", "REPUBLICAN NATIONAL",
    "DSCC", "NRSC", "DCCC", "NRCC",
]

POLITICAL_CAMPAIGN_SERVICES = {
    "RAPID RETURNS", "NGP VAN", "CIVIS ANALYTICS",
    "ACTBLUE TECHNICAL", "BULLY PULPIT INTERACTIVE",
    "PROGRESSIVE VICTORY",
}

# Patterns that identify joint fundraising / pass-through committees
# regardless of whether the candidate's name appears.
_PASSTHROUGH_PATTERNS = [
    "VICTORY FUND", "VICTORY FU", "VICTORY COMMITTEE",
    "JOINT FUNDRAISING", "SENATE VICTORY", "ACTION FUND",
    " VICTORY 20",
]


def _override_campaign_industry(name_upper: str, industry: str) -> str:
    """Force POLITICAL for campaign-related entities that LLMs misclassify."""
    if industry == "POLITICAL":
        return industry
    if any(p in name_upper for p in POLITICAL_OVERRIDE_PATTERNS):
        return "POLITICAL"
    if name_upper in POLITICAL_CAMPAIGN_SERVICES:
        return "POLITICAL"
    return industry


def _override_donor_type(name_upper: str, donor_type: str) -> str:
    """Force CandidateAffiliated for pass-through fundraising vehicles."""
    if donor_type == "CandidateAffiliated":
        return donor_type
    if any(p in name_upper for p in _PASSTHROUGH_PATTERNS):
        return "CandidateAffiliated"
    return donor_type


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
    Also catches joint fundraising committees (e.g. "Cantwell-Warren 2012").
    """
    if not candidate_name:
        return False

    last_name = candidate_name.split(",")[0].strip().upper()
    if len(last_name) <= 2 or last_name not in name_upper:
        return False

    if any(p in name_upper for p in SELF_PAC_PATTERNS):
        return True

    # Joint fundraising committees: "LastName-LastName YEAR" or "LastName LastName YEAR"
    if re.search(r"\b20\d{2}\b", name_upper):
        return True

    return False


def normalize_finance(
    candidate: dict | None,
    financials: list[dict],
    individual_receipts: list[dict],
    pac_receipts: list[dict],
    aggregated_contributors: list[dict],
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
) -> dict:
    """Normalize FEC financial data into the Senator funding shape.

    Args:
        candidate: FEC candidate record.
        financials: FEC financial totals (by cycle).
        individual_receipts: Individual contribution receipts (Schedule A, is_individual=true).
        pac_receipts: PAC/committee contribution receipts (Schedule A, is_individual=false).
        aggregated_contributors: Top contributors by total.
        ai_classifications: Optional AI classifications for donors (type + industry).

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
        pac_receipts,
        individual_receipts,
        aggregated_contributors,
        candidate_name,
        ai_classifications=ai_classifications,
        db_session=db_session,
    )

    # Build industry breakdown: individuals get explicit buckets, PACs get industry-classified
    industry_breakdown = _build_industry_breakdown(
        pac_receipts=pac_receipts,
        individual_receipts=individual_receipts,
        aggregated_contributors=aggregated_contributors,
        small_individual_total=small_individual,
        large_individual_total=large_individual,
        total_raised=total_raised,
        ai_classifications=ai_classifications,
        db_session=db_session,
    )

    computed_pac_total = sum(
        d["total"] for d in top_donors
        if d.get("type") in ("PAC", "SuperPAC", "Party/Ideological")
    )

    return {
        "totalRaised": round(total_raised),
        "totalFromPACs": round(computed_pac_total),
        "smallDonorPercentage": small_donor_percentage,
        "topDonors": top_donors,
        "industryBreakdown": industry_breakdown,
    }


def build_top_donors(
    pac_receipts: list[dict],
    individual_receipts: list[dict],
    aggregated_contributors: list[dict],
    candidate_name: str,
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
) -> list[dict]:
    """Build top donors list prioritizing PAC/corporate money.

    PAC contributions show up directly by committee name.
    Individual contributions are grouped by employer to show corporate influence.

    Args:
        ai_classifications: Optional AI-based donor classifications mapping
            donor name (uppercase) -> {type, industry, skip}
    """
    donor_map: dict[str, dict] = {}
    ai_classifications = ai_classifications or {}

    # 1. Process PAC/committee contributions -- these are the direct corporate money
    for r in pac_receipts:
        name = r.get("contributor_name") or ""
        if not name:
            committee = r.get("committee") or {}
            name = committee.get("name", "")
        if not name or name == "Unknown":
            continue

        name_upper = name.upper().strip()

        # Skip inter-committee transfers
        memo_text = (r.get("memo_text") or "").upper()
        if (
            "TRANSFER" in memo_text
            or "REDESIGNATION" in memo_text
            or "REATTRIBUTION" in memo_text
        ):
            continue

        # Hard skip for payment processors — always filter regardless of AI
        if any(p in name_upper for p in SKIP_PAC_PATTERNS):
            continue

        # Use AI classification if available, otherwise fall back to hardcoded patterns
        ai_class = ai_classifications.get(name_upper)

        if ai_class and ai_class.get("skip"):
            continue

        if ai_class:
            donor_type = ai_class.get("type", "PAC")
            industry = _override_campaign_industry(
                name_upper, ai_class.get("industry", "OTHER")
            )
        else:

            if _is_candidate_affiliated(name_upper, candidate_name):
                donor_type = "CandidateAffiliated"
            elif any(p in name_upper for p in GENERIC_PARTY_PAC_PATTERNS):
                donor_type = "Party/Ideological"
            elif any(p in name_upper for p in CORPORATE_ORG_PATTERNS):
                donor_type = "Org/Employees"
            else:
                donor_type = "PAC"

            industry, _ = classify_with_learning(name, db_session)

        # Catch candidate-affiliated entities the AI missed
        if _is_candidate_affiliated(name_upper, candidate_name):
            donor_type = "CandidateAffiliated"
        donor_type = _override_donor_type(name_upper, donor_type)

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

        # Use AI classification if available
        ai_class = ai_classifications.get(employer)

        if ai_class:
            donor_type = ai_class.get("type", "Org/Employees")
            industry = _override_campaign_industry(
                employer, ai_class.get("industry", "OTHER")
            )
        else:
            donor_type = "Org/Employees"
            industry, _ = classify_with_learning(r.get("contributor_employer", ""), db_session)
            industry = _override_campaign_industry(employer, industry)

        donor_type = _override_donor_type(employer, donor_type)

        existing = donor_map.get(employer, {
            "name": r.get("contributor_employer", ""),
            "total": 0,
            "type": donor_type,
            "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        # Don't overwrite PAC type if we already have PAC entries for this org
        if existing["type"] != "PAC":
            existing["type"] = donor_type
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
            # Use AI classification if available
            ai_class = ai_classifications.get(normalized_name)

            if ai_class:
                donor_type = ai_class.get("type", "PAC")
                industry = ai_class.get("industry", "OTHER")
            else:
                # Fall back to learning store + embedding classifier
                donor_type = "PAC" if c.get("committee_id") else "Org/Employees"
                industry, _ = classify_with_learning(name, db_session)

            donor_map[normalized_name] = {
                "name": name,
                "total": c.get("total", 0) or 0,
                "type": donor_type,
                "industry": industry,
            }

    # Sort by total, take top 100 for detailed industry breakdown
    # (Frontend will show top donors, but all 100 are available for expand/collapse)
    sorted_donors = sorted(donor_map.values(), key=lambda d: d["total"], reverse=True)
    return [
        {
            "name": _clean_donor_name(d["name"]),
            "total": round(d["total"]),
            "type": d["type"],
            "industry": d.get("industry", "OTHER"),
        }
        for d in sorted_donors[:100]
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
    individual_receipts: list[dict],
    aggregated_contributors: list[dict],
    small_individual_total: float,
    large_individual_total: float,
    total_raised: float,
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
) -> list[dict]:
    """Build a funding breakdown showing all sources by industry.

    Small/large unclassified individual donors get explicit buckets.
    PAC receipts and employer-grouped contributions are classified by industry
    to show corporate influence across all contribution types.

    Args:
        ai_classifications: Optional AI-based donor classifications mapping
            donor name (uppercase) -> {type, industry, skip}
    """
    industry_totals: dict[str, dict] = {}
    ai_classifications = ai_classifications or {}

    counted_donors: set[str] = set()

    if small_individual_total > 0:
        industry_totals["SMALL_DONORS"] = {
            "industry": "SMALL_DONORS",
            "name": "SMALL_DONORS",
            "total": small_individual_total,
        }

    # Classify PAC/committee receipts by industry.
    # Skip pass-through entities (victory funds, joint fundraising, candidate
    # PACs) — their money originates from individuals already counted above.
    for r in pac_receipts:
        org = r.get("contributor_name") or r.get("contributor_organization_name") or ""
        if not org:
            continue
        org_upper = org.upper().strip()

        if any(p in org_upper for p in SKIP_PAC_PATTERNS):
            continue

        ai_class = ai_classifications.get(org_upper)

        if ai_class and ai_class.get("skip"):
            continue

        # Skip candidate-affiliated pass-through entities from the breakdown
        ai_type = (ai_class or {}).get("type", "")
        if ai_type == "CandidateAffiliated":
            continue
        if any(p in org_upper for p in POLITICAL_OVERRIDE_PATTERNS):
            continue

        amount = r.get("contribution_receipt_amount", 0) or 0

        if ai_class:
            industry = _override_campaign_industry(
                org_upper, ai_class.get("industry", "OTHER")
            )
        else:
            industry, _ = classify_with_learning(org, db_session)
            industry = _override_campaign_industry(org_upper, industry)

        existing = industry_totals.get(industry, {
            "industry": industry,
            "name": industry,
            "total": 0,
        })
        existing["total"] += amount
        industry_totals[industry] = existing
        counted_donors.add(org_upper)

    # Classify individual employer-grouped contributions by industry
    employer_totals: dict[str, float] = {}
    for r in individual_receipts:
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in SKIP_EMPLOYERS:
            continue
        employer_totals[employer] = employer_totals.get(employer, 0) + (
            r.get("contribution_receipt_amount", 0) or 0
        )

    classified_individual_total = 0.0
    for employer, amount in employer_totals.items():
        ai_class = ai_classifications.get(employer)

        if ai_class:
            industry = _override_campaign_industry(
                employer, ai_class.get("industry", "OTHER")
            )
        else:
            industry, _ = classify_with_learning(employer, db_session)
            industry = _override_campaign_industry(employer, industry)

        existing = industry_totals.get(industry, {
            "industry": industry,
            "name": industry,
            "total": 0,
        })
        existing["total"] += amount
        industry_totals[industry] = existing
        counted_donors.add(employer)
        classified_individual_total += amount

    # Remainder: large individual money not matched to any employer
    unclassified_large = large_individual_total - classified_individual_total
    if unclassified_large > 1000:
        industry_totals["LARGE_INDIVIDUAL"] = {
            "industry": "LARGE_INDIVIDUAL",
            "name": "LARGE_INDIVIDUAL",
            "total": unclassified_large,
        }

    # Add aggregated contributors that we haven't already counted from detailed receipts
    # (Aggregated data may include contributions we don't have in detailed receipts due to pagination)
    logger.info(
        "Industry breakdown: %d donors from detailed receipts, checking %d aggregated",
        len(counted_donors),
        len(aggregated_contributors)
    )
    added_from_aggregated = 0
    for c in aggregated_contributors:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue

        normalized_name = name.upper().strip()

        # Skip if we already counted this donor from detailed receipts
        if normalized_name in counted_donors:
            logger.debug("Skipping aggregated donor (already counted): %s", name)
            continue

        amount = c.get("total", 0) or 0
        added_from_aggregated += 1

        ai_class = ai_classifications.get(normalized_name)

        if ai_class and ai_class.get("skip"):
            continue

        ai_type = (ai_class or {}).get("type", "")
        if ai_type == "CandidateAffiliated":
            continue
        if any(p in normalized_name for p in SKIP_PAC_PATTERNS):
            continue
        if any(p in normalized_name for p in POLITICAL_OVERRIDE_PATTERNS):
            continue

        if ai_class:
            industry = _override_campaign_industry(
                normalized_name, ai_class.get("industry", "OTHER")
            )
        else:
            industry, _ = classify_with_learning(name, db_session)
            industry = _override_campaign_industry(normalized_name, industry)

        # Add to industry totals
        existing = industry_totals.get(industry, {
            "industry": industry,
            "name": industry,
            "total": 0,
        })
        existing["total"] += amount
        industry_totals[industry] = existing

    # Convert to list with percentages, drop zero entries.
    # Percentages are relative to total_raised (all FEC receipts), not just classified funds,
    # so they intentionally sum to <100% when some receipts are unclassified.
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
    return breakdown[:20]  # Top 20 buckets (increased for detailed breakdown)
