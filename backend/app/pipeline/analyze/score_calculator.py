"""
Score calculator — computes the five representation sub-scores from real data.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))


def calculate_scores(senator: dict, flip_flop_result: dict | None) -> dict:
    """
    Calculate the five representation sub-scores from real data.

    Args:
        senator: Assembled senator data (funding, votingRecord, lobbyingMatches).
        flip_flop_result: LLM analysis result (unused; kept for signature compat).

    Returns:
        Dict with the five representationScore sub-fields.
    """
    return {
        "constituentFunding": _calc_constituent_funding(senator.get("funding", {})),
        "independenceIndex": _calc_independence_index(
            senator.get("votingRecord", {}),
            senator.get("lobbyingMatches", []),
        ),
        "donorDiversity": _calc_donor_diversity(
            senator.get("funding", {}).get("industryBreakdown", [])
        ),
        "promiseFulfillment": _calc_promise_fulfillment(
            senator.get("votingRecord", {}),
            senator.get("party", "I"),
        ),
        "accountability": _calc_accountability(senator),
    }


def _calc_constituent_funding(funding: dict) -> int:
    """
    Constituent Funding Score (0-100, higher = better).

    Measures how much of a senator's funding comes from small individual
    donors vs. PACs and large corporate sources.

    - 50% weight: inverse of PAC ratio (less PAC money = higher score)
    - 50% weight: small donor percentage (more small donors = higher score)
    """
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return 50  # neutral default when no funding data

    pac_ratio = funding.get("totalFromPACs", 0) / total_raised
    small_donor_pct = funding.get("smallDonorPercentage", 0) / 100

    # Both factors should reward low PAC / high small-donor
    raw = (1 - pac_ratio) * 0.5 + small_donor_pct * 0.5

    return clamp(raw * 100)


def _calc_independence_index(
    voting_record: dict, lobbying_matches: list[dict]
) -> int:
    """
    Independence Index (0-100, higher = better).

    Measures how independently a senator votes relative to registered lobbyist
    positions. A high score means the senator frequently votes against lobbying
    pressure; a low score means they consistently side with lobbyists.
    """
    if not lobbying_matches or len(lobbying_matches) == 0:
        return 75  # default: moderately independent when no lobbying data

    aligned = sum(
        1 for m in lobbying_matches if m.get("senatorVoteAligned")
    )
    aligned_rate = aligned / len(lobbying_matches)

    # Invert: aligned with lobbyists = lower independence
    return clamp((1 - aligned_rate) * 100)


def _calc_donor_diversity(industry_breakdown: list[dict]) -> int:
    """
    Donor Diversity Score (0-100, higher = better).

    Inverse of the Herfindahl-Hirschman Index — diverse funding sources score
    high; senators captured by a single industry score low.

    Excludes the "OTHER" catch-all bucket (unclassified donations).
    """
    known = [ind for ind in industry_breakdown if ind.get("industry") != "OTHER"]
    if not known:
        return 50  # neutral default when no industry data

    total_known_pct = sum(ind.get("percentage", 0) for ind in known)
    if total_known_pct < 1:
        return 50

    hhi = sum(
        (ind.get("percentage", 0) / total_known_pct) ** 2 for ind in known
    )

    # HHI: 0.2 (5 even industries) → 1.0 (monopoly).
    # Scale to 0-100 concentration score, then invert.
    normalized_concentration = min((hhi - 0.2) / 0.8, 1.0)
    return clamp((1 - normalized_concentration) * 100)


def _calc_promise_fulfillment(voting_record: dict, party: str) -> int:
    """
    Promise Fulfillment Score (0-100, higher = better).

    Measures how consistently a senator votes in line with their stated platform.
    Placeholder: uses party loyalty percentage as a proxy for platform fidelity,
    since most senators run on explicit party platforms.

    - Party members (R/D): party_loyalty_pct from voting record
    - Independents: neutral 50 (no party platform to measure against)
    - No vote data: neutral 50
    """
    if party == "I":
        return 50  # Independents don't have a clear party platform to measure

    total = voting_record.get("totalVotes", 0)
    if total == 0:
        return 50  # neutral default when no vote data

    return clamp(voting_record.get("partyLoyaltyPct", 50))


def _calc_accountability(senator: dict) -> int:
    """
    Accountability Score (0-100, higher = better).

    Measures institutional accountability: senators with highly concentrated
    industry ties, heavy PAC dependence, and decades of revolving-door behavior
    score lower; those with broad constituent support score higher.

    This is the inverse of the prior 'revolving door' heuristic.
    Future: incorporate missed vote rate from GovTrack, outside spending disclosure.
    """
    years_factor = min(senator.get("yearsInOffice", 0) / 30, 1.0)

    industry_breakdown = senator.get("funding", {}).get("industryBreakdown", [])
    known_industries = [i for i in industry_breakdown if i.get("industry") != "OTHER"]
    top_industry_pct = (
        known_industries[0].get("percentage", 0) / 100
        if known_industries
        else 0
    )

    funding = senator.get("funding", {})
    total_raised = funding.get("totalRaised", 0)
    pac_factor = (
        funding.get("totalFromPACs", 0) / total_raised
        if total_raised > 0
        else 0
    )

    # Higher years + industry capture + PAC ratio = lower accountability
    raw_capture = years_factor * 0.3 + top_industry_pct * 0.4 + pac_factor * 0.3
    return clamp((1 - raw_capture) * 100)
