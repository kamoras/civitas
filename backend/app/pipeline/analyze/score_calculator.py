"""
Score calculator — computes the five representation sub-scores from real data.

Higher score = better representation of constituents.
All scores are 0-100 where 100 = ideal representative, 0 = fully captured.

Design principles
-----------------
- Each sub-score should measure a *distinct* dimension of representation.
- Formulas should be transparent and auditable — no black-box LLM scoring.
- Missing data should yield a neutral 50, never a perfect 100 or a 0,
  so that incomplete data doesn't artificially inflate or deflate a senator.
- Seniority alone is never penalized; only *behavioral* signals matter.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

NON_INDUSTRY_CODES = {"OTHER", "SMALL_DONORS", "LARGE_INDIVIDUAL", "POLITICAL"}


def clamp(value: float, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a value to [min_val, max_val] and round to int."""
    return max(min_val, min(max_val, round(value)))


def calculate_scores(senator: dict, flip_flop_result: dict | None) -> dict:
    """
    Calculate the five representation sub-scores from real data.

    Args:
        senator: Assembled senator data (funding, votingRecord, lobbyingMatches).
        flip_flop_result: LLM flip-flop analysis (flipFlopScore, examples).

    Returns:
        Dict with the five representationScore sub-fields.
    """
    voting_record = senator.get("votingRecord", {})
    funding = senator.get("funding", {})
    lobbying_matches = senator.get("lobbyingMatches", [])

    voting_record_with_funding = {**voting_record, "_funding": funding}

    return {
        "constituentFunding": _calc_constituent_funding(funding),
        "independenceIndex": _calc_independence_index(
            voting_record_with_funding,
            lobbying_matches,
        ),
        "donorDiversity": _calc_donor_diversity(
            funding.get("industryBreakdown", [])
        ),
        "promiseFulfillment": _calc_promise_fulfillment(
            voting_record,
            senator.get("party", "I"),
            senator.get("campaignPromises", []),
            flip_flop_result,
        ),
        "accountability": _calc_accountability(senator, lobbying_matches),
    }


def _calc_constituent_funding(funding: dict) -> int:
    """
    Constituent Funding Score (0-100, higher = better).

    Measures how much of a senator's funding comes from small individual
    donors vs. PACs and large corporate sources.

    - 50% weight: inverse of PAC ratio (less PAC money = higher score)
    - 50% weight: small donor percentage (more small donors = higher score)

    Returns 50 when no data is available (neutral, not optimistic).
    """
    total_raised = funding.get("totalRaised", 0)
    if not total_raised or total_raised == 0:
        return 50

    pac_ratio = funding.get("totalFromPACs", 0) / total_raised
    small_donor_pct = funding.get("smallDonorPercentage", 0) / 100

    raw = (1 - pac_ratio) * 0.5 + small_donor_pct * 0.5

    return clamp(raw * 100)


def _calc_independence_index(
    voting_record: dict, lobbying_matches: list[dict]
) -> int:
    """
    Independence Index (0-100, higher = better).

    Measures whether a senator's votes appear influenced by their donors.
    Two signals are combined multiplicatively so that *either alone* does
    not penalize, but *both together* do:

      capture_rate  = donor_aligned_votes / scoreable_votes   (0-1)
      pac_ratio     = total_from_PACs / total_raised           (0-1)

    Additionally, lobbying match data provides a direct signal: when donors
    give money AND the senator votes in their favor on related bills, that
    is a stronger indicator than generic donor-alignment.

    Formula:
      base       = 100 * (1 - capture_rate * pac_ratio)
      lobby_pen  = aligned_matches / total_matches * 20  (up to -20 points)
      score      = base - lobby_pen

    Returns 50 when no data is available (neutral).
    """
    donor_aligned = voting_record.get("donorAlignedVotes", 0)
    donor_opposed = voting_record.get("donorOpposedVotes", 0)
    scoreable = donor_aligned + donor_opposed

    funding = voting_record.get("_funding", {})
    total_raised = funding.get("totalRaised", 0)
    pac_ratio = (
        funding.get("totalFromPACs", 0) / total_raised
        if total_raised > 0
        else 0
    )

    if scoreable == 0 and not lobbying_matches:
        return 50  # no data → neutral

    # Base score from vote/funding joint signal
    if scoreable > 0:
        capture_rate = donor_aligned / scoreable
        base = (1 - capture_rate * pac_ratio) * 100
    else:
        base = 100

    # Lobbying alignment penalty: direct donor→vote connections are the
    # strongest signal we have.  Each aligned match pulls the score down
    # proportionally, capped at -25 points.
    if lobbying_matches:
        aligned = sum(1 for m in lobbying_matches if m.get("senatorVoteAligned"))
        total_matches = len(lobbying_matches)
        if total_matches > 0:
            lobby_alignment_rate = aligned / total_matches
            base -= lobby_alignment_rate * 25

    return clamp(base)


def _calc_donor_diversity(industry_breakdown: list[dict]) -> int:
    """
    Donor Diversity Score (0-100, higher = better).

    Uses the inverse Herfindahl-Hirschman Index on *real industry* buckets
    only — excluding OTHER (unclassified), SMALL_DONORS, LARGE_INDIVIDUAL,
    and POLITICAL (party committees), since those aren't industry-specific
    influence channels.

    Returns 50 when no classifiable industry data exists (neutral).
    """
    industries = [
        ind for ind in industry_breakdown
        if ind.get("industry") not in NON_INDUSTRY_CODES
    ]
    if not industries:
        return 50

    total_known_pct = sum(ind.get("percentage", 0) for ind in industries)
    if total_known_pct < 1:
        return 50

    hhi = sum(
        (ind.get("percentage", 0) / total_known_pct) ** 2 for ind in industries
    )

    # HHI ranges from 1/N (perfectly even) to 1.0 (monopoly).
    # With 5+ even industries HHI ≈ 0.2; we use that as the "ideal" floor.
    normalized_concentration = min((hhi - 0.2) / 0.8, 1.0)
    return clamp((1 - normalized_concentration) * 100)


def _calc_promise_fulfillment(
    voting_record: dict,
    party: str,
    campaign_promises: list[dict] | None = None,
    flip_flop_result: dict | None = None,
) -> int:
    """
    Promise Fulfillment Score (0-100, higher = better).

    Primary: if campaign promise data is available (from platform_analyzer),
    use the ratio of kept/partial promises to total scoreable promises.

    Fallback chain when no platform data exists:
      1. Flip-flop score (inverted) — measures legislative consistency,
         which is a reasonable proxy for keeping commitments.
      2. Neutral 50 — honest "we don't know" rather than a misleading
         party-loyalty proxy.

    Scoring for promises:
      kept    = 1.0 point
      partial = 0.5 point
      broken  = 0.0 point
      unclear = excluded (not enough data to judge)
    """
    if campaign_promises:
        scoreable = [
            p for p in campaign_promises
            if p.get("alignment") in ("kept", "broken", "partial")
        ]
        if scoreable:
            score = sum(
                1.0 if p["alignment"] == "kept"
                else 0.5 if p["alignment"] == "partial"
                else 0.0
                for p in scoreable
            )
            return clamp(score / len(scoreable) * 100)

    # Fallback 1: flip-flop score (0 = consistent, 100 = inconsistent)
    if flip_flop_result and isinstance(flip_flop_result, dict):
        ff_score = flip_flop_result.get("flipFlopScore")
        if ff_score is not None and isinstance(ff_score, (int, float)):
            return clamp(100 - ff_score)

    # Fallback 2: honest neutral — we don't have enough data to judge
    return 50


def _calc_accountability(senator: dict, lobbying_matches: list[dict]) -> int:
    """
    Accountability Score (0-100, higher = better).

    Measures behavioral signals of institutional capture — NOT tenure.
    Seniority alone is never penalized; only the combination of funding
    patterns and voting behavior matters.

    Three factors:
      1. Missed vote rate (40%): senators who don't show up aren't
         representing anyone.  Calculated from total tracked votes vs
         "Not Voting" entries.
      2. Lobbying alignment (30%): when donors have direct connections
         to bills and the senator votes in their favor, that's a red flag.
      3. PAC dependency ratio (30%): high PAC ratio combined with low
         small-donor support suggests institutional rather than constituent
         backing.

    Returns 50 when insufficient data exists (neutral).
    """
    funding = senator.get("funding", {})
    total_raised = funding.get("totalRaised", 0)

    voting_record = senator.get("votingRecord", {})
    all_key_votes = voting_record.get("keyVotes", [])
    has_vote_data = isinstance(all_key_votes, list) and len(all_key_votes) > 0
    has_funding_data = total_raised > 0

    if not has_vote_data and not has_funding_data and not lobbying_matches:
        return 50  # no data → neutral

    # Factor 1: Missed vote rate (from key + recent votes)
    if has_vote_data:
        not_voting_count = sum(
            1 for v in all_key_votes
            if (v.get("vote") if isinstance(v, dict) else getattr(v, "vote", None)) == "Not Voting"
        )
        total = len(all_key_votes)
        participation_rate = (total - not_voting_count) / total if total > 0 else 1.0
    else:
        participation_rate = 1.0  # no data → assume present (neutral)

    # Factor 2: Lobbying alignment
    if lobbying_matches:
        aligned = sum(1 for m in lobbying_matches if m.get("senatorVoteAligned"))
        lobby_rate = aligned / len(lobbying_matches) if lobbying_matches else 0
    else:
        lobby_rate = 0  # no data → no penalty

    # Factor 3: PAC dependency (inverse)
    if total_raised > 0:
        pac_factor = funding.get("totalFromPACs", 0) / total_raised
    else:
        pac_factor = 0

    # Combine: each factor yields 0-1 where 1 = worst
    raw_penalty = (
        (1 - participation_rate) * 0.4
        + lobby_rate * 0.3
        + pac_factor * 0.3
    )
    return clamp((1 - raw_penalty) * 100)
