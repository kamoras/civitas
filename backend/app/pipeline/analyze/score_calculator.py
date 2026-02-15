"""
Score calculator — computes the five corruption sub-scores from real data.
These feed into the weighted formula in src/lib/corruption.ts.
All pure math, no LLM calls.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))


def calculate_scores(senator: dict, flip_flop_result: dict | None) -> dict:
    """
    Calculate the five corruption sub-scores from real data.

    Args:
        senator: Assembled senator data (funding, votingRecord, lobbyingMatches).
        flip_flop_result: LLM flip-flop analysis result with flipFlopScore key.

    Returns:
        Dict with the five corruptionScore sub-fields.
    """
    return {
        "corporateFunding": _calc_corporate_funding(senator.get("funding", {})),
        "lobbyistAlignment": _calc_lobbyist_alignment(
            senator.get("votingRecord", {}),
            senator.get("lobbyingMatches", []),
        ),
        "industryConcentration": _calc_industry_concentration(
            senator.get("funding", {}).get("industryBreakdown", [])
        ),
        "flipFlopIndex": (
            flip_flop_result.get("flipFlopScore", 25)
            if flip_flop_result
            else 25
        ),
        "revolvingDoor": _calc_revolving_door(senator),
    }


def _calc_corporate_funding(funding: dict) -> int:
    """
    Corporate Funding Score (0-100).
    Based on PAC funding ratio and total from corporate sources.
    """
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return 0

    pac_ratio = funding.get("totalFromPACs", 0) / total_raised
    small_donor_inverse = 1 - funding.get("smallDonorPercentage", 0) / 100

    # Weighted: 60% PAC ratio, 40% inverse small donor percentage
    raw = pac_ratio * 0.6 + small_donor_inverse * 0.4

    # Scale to 0-100
    return clamp(raw * 100)


def _calc_lobbyist_alignment(
    voting_record: dict, lobbying_matches: list[dict]
) -> int:
    """
    Lobbyist Alignment Score (0-100).
    Percentage of lobbying matches where senator voted with the lobby position.
    """
    if not lobbying_matches or len(lobbying_matches) == 0:
        return 25  # Default moderate-low

    aligned = sum(
        1 for m in lobbying_matches if m.get("senatorVoteAligned")
    )
    rate = aligned / len(lobbying_matches)

    return clamp(rate * 100)


def _calc_industry_concentration(industry_breakdown: list[dict]) -> int:
    """
    Industry Concentration Score (0-100).
    Uses Herfindahl-Hirschman Index of industry donation shares.
    High concentration = funding dominated by few industries = higher score.
    """
    if not industry_breakdown or len(industry_breakdown) == 0:
        return 0

    # Calculate HHI from percentage shares
    hhi = sum(
        (ind.get("percentage", 0) / 100) ** 2 for ind in industry_breakdown
    )

    # HHI ranges from ~0.05 (diverse) to 1.0 (monopoly)
    # Scale: 0.05 -> 0, 0.5 -> 100
    normalized = min((hhi - 0.05) / 0.45, 1.0)

    return clamp(normalized * 100)


def _calc_revolving_door(senator: dict) -> int:
    """
    Revolving Door Score (0-100).
    Based on years in office and industry ties.
    Long-serving senators with concentrated industry funding score higher.
    """
    # Simplified heuristic. In v2, the LLM would analyze hearing transcripts
    # for mentions of prior/future industry employment.
    years_factor = min(senator.get("yearsInOffice", 0) / 30, 1.0)

    industry_breakdown = senator.get("funding", {}).get("industryBreakdown", [])
    top_industry_pct = (
        industry_breakdown[0].get("percentage", 0) / 100
        if industry_breakdown
        else 0
    )

    funding = senator.get("funding", {})
    total_raised = funding.get("totalRaised", 0)
    pac_factor = (
        funding.get("totalFromPACs", 0) / total_raised
        if total_raised > 0
        else 0
    )

    raw = years_factor * 0.3 + top_industry_pct * 0.4 + pac_factor * 0.3
    return clamp(raw * 100)
