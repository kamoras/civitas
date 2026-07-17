"""
Senator analyzer — embedding-based classification, fully deterministic.

  - Lobbying match detection via donor↔vote embedding similarity
  - Key vote selection via donor↔policy embedding similarity

Two features that used to live here were removed entirely after live
audits found their output unreliable regardless of extraction/generation
method:

  - Campaign-promise tracking (2026-07) — see policy_alignment.py's
    module docstring.
  - LLM narrative generation — votingSummary, per-vote reasoning,
    pacDetails, platformSummary (2026-07) — see analyze_senator_batch's
    docstring. House never had this narrative and shipped fine without
    it; Senate now matches.
"""

import logging
from typing import Any

from app.pipeline.analyze.policy_alignment import (
    detect_donor_vote_connections,
    get_related_policies,
)

logger = logging.getLogger(__name__)


# ── Embedding-based lobbying match detection ─────────────────────


def detect_lobbying_matches(
    donors: list[dict],
    all_votes: list[dict],
    industry_breakdown: list[dict] | None = None,
) -> list[dict]:
    """Detect donor-vote connections: substantial industry funding share
    (of classifiable industry money) matched against policy-area-anchored
    vote similarity. See detect_donor_vote_connections's docstring for the
    full two-stage gate rationale.
    """
    return detect_donor_vote_connections(donors, all_votes, industry_breakdown)


# ── Embedding-based key vote selection ───────────────────────────


def select_key_votes(
    all_votes: list[dict],
    donors: list[dict],
    max_keys: int = 7,
) -> list[str]:
    """Select the most notable votes using embedding-derived policy relevance.

    Scoring heuristic (higher = more notable):
      +3  voted against party line
      +2  policy area related to a top donor's industry (via embedding similarity)
      +1  non-procedural substantive vote
    """
    external = [d for d in donors if d.get("type") not in ("CandidateAffiliated", "Self-Funded", "SKIP")]
    donor_policies: set[str] = set()
    for d in external[:8]:
        ind = d.get("industry", "OTHER")
        if ind in ("OTHER", "POLITICAL", "SMALL_DONORS", "LARGE_INDIVIDUAL"):
            continue
        donor_policies.update(get_related_policies(ind))

    scored: list[tuple[float, str]] = []
    for v in all_votes:
        if v.get("vote") not in ("Yea", "Nay"):
            continue
        if v.get("policyArea", "PROCEDURAL") == "PROCEDURAL":
            continue

        score = 1.0
        if v.get("votedWithParty") is False:
            score += 3.0
        vote_areas = {
            a.get("area") for a in v.get("policyAreas", [])
        } or {v.get("policyArea", "")}
        if vote_areas & donor_policies:
            score += 2.0
        scored.append((score, v["billId"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [bid for _, bid in scored[:max_keys]]


# ── Public API ───────────────────────────────────────────────────


def precompute_senator_analysis(item: dict) -> dict:
    """Compute a senator's embedding-based analysis (lobbying detection,
    key vote selection) ahead of analyze_senator_batch, which accepts
    the result via its `precomputed` param to skip redoing the work.

    Used to live in a separate background thread overlapping this
    embedding work with the narrative LLM call analyze_senator_batch
    used to make — removed entirely (2026-07, see
    analyze_senator_batch's docstring), so there's nothing left to
    overlap with. Kept as a separate function anyway since the split
    still expresses a real distinction (deterministic classification
    vs. the batch wrapper), just called synchronously now.
    """
    donors = item.get("donors", [])
    all_votes = item.get("allVotes", [])
    industry_breakdown = item.get("industryBreakdown", [])

    has_data = len(donors) > 0 or len(all_votes) > 0

    lobbying_matches = (
        detect_lobbying_matches(donors, all_votes, industry_breakdown)
        if has_data else []
    )
    key_vote_ids = select_key_votes(all_votes, donors) if has_data else []

    return {
        "lobbyingMatches": lobbying_matches,
        "keyVoteIds": key_vote_ids,
    }


async def analyze_senator_batch(
    batch: list[dict],
    db_session: Any | None = None,
    precomputed: dict | None = None,
) -> list[dict]:
    """Classify senators: embedding-based classification only.

    When precomputed data is provided (from precompute_senator_analysis),
    skips the embedding work.

    Classification (deterministic, embedding-based):
      - Lobbying matches via donor↔vote similarity
      - Key vote selection via donor↔policy similarity

    votingSummary, reasoning, pacDetails, and platformSummary are always
    empty: the narrative-generation LLM call was removed entirely
    (2026-07) after a real measurement (63s/call, ~1.7h across the full
    Senate) combined with sampled production output that was either
    generic boilerplate or, for per-vote reasoning, occasionally
    fabricated/wrong (e.g. a defense bill's fiscal year misstated by a
    year; a veterans-affairs bill's "reasoning" describing an unrelated
    health-savings-account policy). House has shipped without this
    narrative since inception with no equivalent complaint.

    campaignPromises is always []: a separate, earlier removal for the
    same underlying reliability reason — see policy_alignment.py's
    module docstring.
    """
    results: list[dict] = []

    for item in batch:
        senator = item["senator"]
        donors = item.get("donors", [])
        key_votes = item.get("keyVotes", [])
        all_votes = item.get("allVotes", [])
        industry_breakdown = item.get("industryBreakdown", [])

        has_data = len(donors) > 0 or len(key_votes) > 0

        if precomputed:
            lobbying_matches = precomputed["lobbyingMatches"]
            key_vote_ids = precomputed["keyVoteIds"]
        else:
            lobbying_matches = (
                detect_lobbying_matches(donors, all_votes, industry_breakdown)
                if has_data else []
            )
            key_vote_ids = select_key_votes(all_votes, donors) if has_data else []

        results.append({
            "senatorId": senator["id"],
            "keyVotes": key_votes,
            "lobbyingMatches": lobbying_matches,
            "keyVoteIds": key_vote_ids,
            "reasoning": {},
            "votingSummary": "",
            "pacDetails": [],
            "platformSummary": "",
            "campaignPromises": [],
        })

    return results


