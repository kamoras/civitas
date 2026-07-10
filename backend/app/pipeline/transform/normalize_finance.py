"""Normalize FEC financial data into the Senator funding shape.

Donor type and industry classification are handled by the AI classifier
pipeline (donor_classifier_ai.py) using FEC metadata, sentence-transformer
embeddings, and a kNN learning store. This module focuses purely on
normalizing the financial data structure — it does NOT re-derive donor
types from hardcoded patterns.

When the AI classifier has a result, it's used directly. For entities
not in the AI results (edge cases), it falls back to the embedding-based
industry classifier.
"""

import logging

from app.pipeline.fetch.fec import select_recent_elections
from app.pipeline.transform.candidate_names import is_candidate_self_donor
from app.pipeline.transform.industry_classifier import classify_with_learning
from app.pipeline.analyze.donor_classifier_ai import (
    classify_donor_type_semantic,
    classify_employer_skips_batch,
    classify_transfer_memos_batch,
    is_skip_entity,
)

logger = logging.getLogger(__name__)


def _is_contribution_row(receipt: dict) -> bool:
    """True when a Schedule A row is an actual third-party contribution.

    Schedule A on candidate filings (Form 3) itemizes every receipt, not
    just donations. Only line 11 is contributions (11AI individuals, 11B
    party committees, 11C other committees, 11D the candidate). The rest
    is structurally not donor money and was polluting top-donor lists
    fleet-wide (2026-07 audit): line 12 transfers from joint-fundraising
    committees ($768M cached), 13A/13B loans ($219M — banks that lent to
    campaigns listed as "donors"), 14 offsets to operating expenditures
    ($33M — media-buy refunds, e.g. a vendor as a senator's top donor),
    15 other receipts ($73M — bank interest), and 17A conduit totals
    (WinRed aggregates whose underlying gifts are already itemized on
    11AI). Rows without a line_number are kept — the memo-text and
    donor-type classifiers still screen those.
    """
    line = receipt.get("line_number")
    if line is None or line == "":
        return True
    return str(line).startswith("11")


def _is_candidate_line(receipt: dict) -> bool:
    """Line 11D — contributions from the candidate themselves."""
    return str(receipt.get("line_number") or "") == "11D"


def normalize_finance(
    candidate: dict | None,
    financials: list[dict],
    individual_receipts: list[dict],
    pac_receipts: list[dict],
    aggregated_contributors: list[dict],
    ai_classifications: dict[str, dict] | None = None,
    db_session=None,
    outside_spending: dict | None = None,
) -> dict:
    """Normalize FEC financial data into the Senator funding shape.

    Args:
        candidate: FEC candidate record.
        financials: FEC financial totals (by cycle).
        individual_receipts: Individual contribution receipts (Schedule A, is_individual=true).
        pac_receipts: PAC/committee contribution receipts (Schedule A, is_individual=false).
        aggregated_contributors: Top contributors by total.
        ai_classifications: Optional AI classifications for donors (type + industry).
        outside_spending: Optional outside spending dict with totalFor and count from
            fetch_outside_spending (super PAC independent expenditures supporting the candidate).

    Returns:
        Normalized funding object matching Senator.funding type.
    """
    # Sum across the two most recent elections (one deduped row each —
    # see select_recent_elections for why raw [:2] double-counted).
    recent_cycles = select_recent_elections(financials)

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
        candidate_name=candidate_name,
    )

    computed_pac_total = sum(
        d["total"] for d in top_donors
        if d.get("type") in ("PAC", "SuperPAC", "Party/Ideological")
    )

    # The FEC's cycle totals (other_political_committee_contributions,
    # Schedule A) are the authoritative PAC figure. The classifier-derived
    # sum systematically undercounts when committee donors are typed as
    # Org/Employees — the 2026-06 audit found $34.8K recorded vs millions
    # actual for senior senators, which then scored as funding independence.
    # Use the classifier sum only when the FEC total is missing.
    if total_from_pacs > 0:
        final_pac_total = min(total_from_pacs, total_raised)
    else:
        final_pac_total = min(computed_pac_total, total_raised)

    outside_spending_for = outside_spending.get("totalFor", 0) if outside_spending else 0

    return {
        "totalRaised": round(total_raised),
        "totalFromPACs": round(final_pac_total),
        "smallDonorPercentage": small_donor_percentage,
        "topDonors": top_donors,
        "industryBreakdown": industry_breakdown,
        "outsideSpendingFor": outside_spending_for,
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

    Donor type and industry come from the AI classifier (embedding-based).
    When no AI classification exists, falls back to the embedding-based
    industry classifier and semantic donor-type classifier.
    """
    donor_map: dict[str, dict] = {}
    ai_classifications = ai_classifications or {}

    # Pre-compute embedding-based skip sets for employers and memo texts.
    # This replaces hardcoded SKIP_EMPLOYERS and keyword-based memo filtering
    # with semantic similarity against learned prototypes.
    unique_employers = list({
        (r.get("contributor_employer") or "").upper().strip()
        for r in individual_receipts
        if (r.get("contributor_employer") or "").strip()
    })
    skip_employer_set = classify_employer_skips_batch(unique_employers)

    unique_memos = list({
        (r.get("memo_text") or "").upper().strip()
        for r in pac_receipts
        if (r.get("memo_text") or "").strip()
    })
    transfer_memo_set = classify_transfer_memos_batch(unique_memos)

    def _classify_fallback(name: str, name_upper: str) -> tuple[str, str]:
        """Embedding-based fallback for donors not in AI classifications."""
        dtype = classify_donor_type_semantic(
            name, candidate_name=candidate_name
        ) or "Org/Employees"
        industry, _ = classify_with_learning(name, db_session)
        return dtype, industry

    def _get_classification(name: str, name_upper: str) -> tuple[str, str, bool]:
        """Return (donor_type, industry, should_skip) from AI or fallback."""
        ai_class = ai_classifications.get(name_upper)
        if ai_class:
            if ai_class.get("skip"):
                return "SKIP", "OTHER", True
            return (
                ai_class.get("type", "Org/Employees"),
                ai_class.get("industry", "OTHER"),
                False,
            )
        if is_skip_entity(name_upper):
            return "SKIP", "OTHER", True
        dtype, industry = _classify_fallback(name, name_upper)
        return dtype, industry, False

    # 1. PAC/committee contributions
    for r in pac_receipts:
        if not _is_contribution_row(r):
            continue

        name = r.get("contributor_name") or ""
        if not name:
            committee = r.get("committee") or {}
            name = committee.get("name", "")
        if not name or name == "Unknown":
            continue

        name_upper = name.upper().strip()

        memo_upper = (r.get("memo_text") or "").upper().strip()
        if memo_upper and memo_upper in transfer_memo_set:
            continue

        if _is_candidate_line(r):
            # Line 11D is the candidate's own money by FEC definition —
            # no name heuristic needed.
            donor_type, industry, skip = "Self-Funded", "OTHER", False
        else:
            donor_type, industry, skip = _get_classification(name, name_upper)
        if skip:
            continue

        existing = donor_map.get(name_upper, {
            "name": name, "total": 0, "type": donor_type, "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        donor_map[name_upper] = existing

    # 2. Individual contributions grouped by employer
    for r in individual_receipts:
        if not _is_contribution_row(r):
            continue
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in skip_employer_set:
            continue

        ai_class = ai_classifications.get(employer)
        if ai_class:
            donor_type = ai_class.get("type", "Org/Employees")
            industry = ai_class.get("industry", "OTHER")
        else:
            donor_type = "Org/Employees"
            industry, _ = classify_with_learning(r.get("contributor_employer", ""), db_session)

        existing = donor_map.get(employer, {
            "name": r.get("contributor_employer", ""),
            "total": 0, "type": donor_type, "industry": industry,
        })
        existing["total"] += r.get("contribution_receipt_amount", 0) or 0
        if existing["type"] != "PAC":
            existing["type"] = donor_type
        donor_map[employer] = existing

    # 3. Aggregated contributors as fallback
    for c in aggregated_contributors:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue

        normalized_name = name.upper().strip()
        if normalized_name not in donor_map:
            donor_type, industry, skip = _get_classification(name, normalized_name)
            if skip:
                continue
            donor_map[normalized_name] = {
                "name": name,
                "total": c.get("total", 0) or 0,
                "type": donor_type,
                "industry": industry,
            }

    # The candidate's own money (self-loans recorded as "Lastname,
    # Firstname") is frequently mistyped Org/Employees by the semantic
    # classifier — the 2026-07 audit found 19 senators listed as their own
    # top donor ($19M for one). Deterministically reclassify so downstream
    # consumers (FI concentration, donor-vote matches) can exclude it.
    for d in donor_map.values():
        if d["type"] not in ("Self-Funded", "CandidateAffiliated") and \
                is_candidate_self_donor(d["name"], candidate_name):
            d["type"] = "Self-Funded"

    sorted_donors = sorted(donor_map.values(), key=lambda d: d["total"], reverse=True)
    return [
        {
            "name": _clean_donor_name(d["name"]),
            "total": round(d["total"]),
            "type": d["type"],
            "industry": d.get("industry", "OTHER"),
        }
        for d in sorted_donors
        if d["total"] > 0 and len(d["name"].strip()) >= 3
        # Candidate-affiliated committees (the candidate's own campaign,
        # joint fundraising, and victory committees) are transfers, not
        # donors. Listing them made routine JFC users look captured by
        # their own committees in the concentration score and the UI.
        and d.get("type") != "CandidateAffiliated"
    ][:100]


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
    candidate_name: str = "",
) -> list[dict]:
    """Build a funding breakdown showing all sources by industry.

    Uses AI classifications (embedding-based) for industry. Falls back
    to the embedding classifier for entities without AI results.
    """
    industry_totals: dict[str, dict] = {}
    ai_classifications = ai_classifications or {}
    counted_donors: set[str] = set()

    if small_individual_total > 0:
        industry_totals["SMALL_DONORS"] = {
            "industry": "SMALL_DONORS", "name": "SMALL_DONORS",
            "total": small_individual_total,
        }

    # Pre-compute employer skip set for this breakdown (same approach as build_top_donors)
    unique_employers_bd = list({
        (r.get("contributor_employer") or "").upper().strip()
        for r in individual_receipts
        if (r.get("contributor_employer") or "").strip()
    })
    skip_employer_bd = classify_employer_skips_batch(unique_employers_bd)

    def _get_industry(name: str, name_upper: str) -> str:
        ai_class = ai_classifications.get(name_upper)
        if ai_class:
            return ai_class.get("industry", "OTHER")
        industry, _ = classify_with_learning(name, db_session)
        return industry

    def _should_skip_for_breakdown(name_upper: str) -> bool:
        ai_class = ai_classifications.get(name_upper)
        if ai_class and (
            ai_class.get("skip")
            or ai_class.get("type") in ("CandidateAffiliated", "Self-Funded")
        ):
            return True
        if is_skip_entity(name_upper):
            return True
        # The candidate's own money is not industry money — the semantic
        # classifier misses "Lastname, Firstname" self-loans.
        if candidate_name and is_candidate_self_donor(name_upper, candidate_name):
            return True
        return False

    for r in pac_receipts:
        if not _is_contribution_row(r) or _is_candidate_line(r):
            continue
        org = r.get("contributor_name") or r.get("contributor_organization_name") or ""
        if not org:
            continue
        org_upper = org.upper().strip()

        if _should_skip_for_breakdown(org_upper):
            continue

        amount = r.get("contribution_receipt_amount", 0) or 0
        industry = _get_industry(org, org_upper)

        existing = industry_totals.get(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount
        industry_totals[industry] = existing
        counted_donors.add(org_upper)

    employer_totals: dict[str, float] = {}
    for r in individual_receipts:
        if not _is_contribution_row(r):
            continue
        employer = (r.get("contributor_employer") or "").upper().strip()
        if not employer or employer in skip_employer_bd:
            continue
        if _should_skip_for_breakdown(employer):
            continue
        employer_totals[employer] = employer_totals.get(employer, 0) + (
            r.get("contribution_receipt_amount", 0) or 0
        )

    classified_individual_total = 0.0
    for employer, amount in employer_totals.items():
        industry = _get_industry(employer, employer)

        existing = industry_totals.get(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount
        industry_totals[industry] = existing
        counted_donors.add(employer)
        classified_individual_total += amount

    unclassified_large = large_individual_total - classified_individual_total
    if unclassified_large > 1000:
        industry_totals["LARGE_INDIVIDUAL"] = {
            "industry": "LARGE_INDIVIDUAL", "name": "LARGE_INDIVIDUAL",
            "total": unclassified_large,
        }

    for c in aggregated_contributors:
        name = c.get("contributor_name") or "Unknown"
        if not name or name == "Unknown":
            continue
        normalized_name = name.upper().strip()
        if normalized_name in counted_donors:
            continue
        if _should_skip_for_breakdown(normalized_name):
            continue

        amount = c.get("total", 0) or 0
        industry = _get_industry(name, normalized_name)

        existing = industry_totals.get(industry, {"industry": industry, "name": industry, "total": 0})
        existing["total"] += amount
        industry_totals[industry] = existing

    # Add an UNCLASSIFIED bucket for money not captured by any classification
    raw_total = sum(ind["total"] for ind in industry_totals.values())
    unclassified = total_raised - raw_total
    if unclassified > total_raised * 0.01:
        industry_totals["UNCLASSIFIED"] = {
            "industry": "UNCLASSIFIED", "name": "UNCLASSIFIED",
            "total": unclassified,
        }
        raw_total = total_raised

    denom = raw_total if raw_total > 0 else 1
    breakdown = []
    for ind in industry_totals.values():
        total = round(ind["total"])
        percentage = round((ind["total"] / denom) * 100) if denom > 0 else 0
        if total > 0:
            breakdown.append({
                "industry": ind["industry"],
                "name": ind["industry"].replace("_", " "),
                "total": total,
                "percentage": percentage,
            })

    breakdown.sort(key=lambda x: x["total"], reverse=True)
    return breakdown[:20]
