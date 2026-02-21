"""
Platform analyzer — fetches each senator's stated policy positions from their
official website and cross-references them with actual voting records.

Uses vector search to find semantically relevant bills for each promise.

Produces structured campaignPromises (kept / broken / partial / unclear) and
a plain-English platformSummary.
"""

import logging
from typing import Any

from app.config_definitions import PLATFORM_CATEGORIES
from app.pipeline.analyze.ollama_client import call_llm
from app.pipeline.vector_store import search_bills

logger = logging.getLogger(__name__)


async def analyze_platform_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Analyze campaign platforms and cross-reference with votes for a batch.

    Args:
        batch: List of dicts with keys:
            - senator: base senator record (name, party, state, yearsInOffice)
            - allVotes: all normalized vote dicts for this senator
            - platformText: raw text from the senator's official website (may be "")

    Returns:
        List of dicts with: senatorId, platformSummary, campaignPromises.
    """
    if not batch:
        return []

    results = []
    for item in batch:
        senator = item["senator"]
        all_votes = item.get("allVotes", [])
        platform_text = item.get("platformText", "")

        result = await _analyze_single(senator, all_votes, platform_text, db_session)
        results.append(result)

    return results


async def _analyze_single(
    senator: dict,
    all_votes: list[dict],
    platform_text: str,
    db_session: Any | None,
) -> dict:
    empty = {
        "senatorId": senator["id"],
        "platformSummary": "",
        "campaignPromises": [],
    }

    if not platform_text:
        return empty

    # Use vector search to find bills semantically related to platform text
    # This finds bills across all sessions that match the senator's stated priorities
    logger.info("Searching for bills related to %s's platform...", senator["name"])
    relevant_bills_from_vector = search_bills(
        query=platform_text[:1000],  # Use first 1000 chars of platform as query
        n_results=20,  # Get top 20 semantically similar bills
    )

    # Build a map of bill IDs from vector search for quick lookup
    vector_bill_ids = {b["billId"] for b in relevant_bills_from_vector}

    # Combine vector search results with senator's actual votes
    # Priority: bills from vector search (semantically relevant to platform)
    # Fallback: recent votes if not enough vector matches
    combined_votes = []

    # First, add senator's votes on semantically relevant bills
    for v in all_votes:
        if v.get("billId") in vector_bill_ids:
            combined_votes.append(v)

    # Then fill remaining slots with recent votes
    for v in all_votes:
        if v.get("billId") not in vector_bill_ids and len(combined_votes) < 15:
            combined_votes.append(v)

    # Build rich vote summary including bill descriptions and policy context
    vote_lines = []
    for v in combined_votes[:15]:  # Top 15 most relevant
        bill_name = v.get("billName", "")[:100]  # Increased from 50
        vote = v.get("vote", "")
        bill_id = v.get("billId", "")
        description = v.get("description", "")[:200]  # Add bill description!
        policy_area = v.get("policyArea", "")
        stance = v.get("stance", "")
        groups = v.get("impactedGroups", [])
        groups_str = ", ".join(groups[:3]) if groups else ""

        # Format: billId | billName | description | vote | policy: stance | affects: groups
        vote_lines.append(
            f"{bill_id} | {bill_name}\n"
            f"  What it does: {description}\n"
            f"  Senator voted: {vote} | Policy: {policy_area} ({stance}) | Affects: {groups_str}"
        )
    votes_text = "\n\n".join(vote_lines) if vote_lines else "No votes on record"

    logger.info(
        "Analyzing platform with %d semantically relevant votes (from vector search: %d)",
        len(combined_votes),
        len([v for v in combined_votes if v.get("billId") in vector_bill_ids]),
    )

    categories_str = "|".join(PLATFORM_CATEGORIES.keys())

    result = await call_llm(
        prompt_version="platform-analysis-v3",
        system_prompt="Political analyst. Return ONLY valid JSON, no markdown.",
        user_prompt=(
            f"Senator {senator['name']} ({senator['party']}-{senator['state']}), "
            f"{senator.get('yearsInOffice', 0)} years in office.\n\n"
            f"OFFICIAL PLATFORM (from their website):\n{platform_text[:2500]}\n\n"
            f"RECENT VOTES:\n{votes_text}\n\n"
            f"TASK: Extract 3-5 specific POLICY COMMITMENTS from the platform text. "
            f"A commitment is a promise to take action, support/oppose specific policies, or advocate for change. "
            f"IGNORE general statements of fact or observations.\n\n"
            f"For each commitment, carefully READ the bill descriptions to find votes that relate to the promise. "
            f"Match commitments to votes and determine if kept/broken:\n"
            f"- KEPT: Senator voted consistently with their stated commitment (use bill description to verify)\n"
            f"- BROKEN: Senator voted against their stated commitment (use bill description to verify)\n"
            f"- PARTIAL: Mixed votes - some support, some contradict\n"
            f"- UNCLEAR: No relevant votes found, or commitment too vague to assess, or bill description doesn't clearly relate\n\n"
            f"Return JSON:\n"
            f'{{"platformSummary":"<1-2 sentence summary>",'
            f'"campaignPromises":['
            f'{{"promiseText":"<the actual policy commitment>","category":"<{categories_str}>","alignment":"<kept|broken|partial|unclear>","relatedBills":["<billId from votes above>"],"analysis":"<2-3 sentences: (1) What did they promise? (2) Which specific votes relate to this? (3) How did their votes support or contradict the promise?>"}}'
            f"]}}\n\n"
            f"CRITICAL: In the analysis field:\n"
            f"1. Reference SPECIFIC bill IDs from the votes above\n"
            f"2. Explain HOW the bill description relates to the promise\n"
            f"3. Show whether their vote supported or contradicted the promise\n"
            f"Do NOT mark something as kept/broken without specific bill descriptions that clearly relate to the promise."
        ),
        cache_key={
            "senatorId": senator["id"],
            "platformLen": len(platform_text),
            "voteCount": len(all_votes),
            "v": 3,  # Bumped: now includes bill descriptions in prompt
        },
        db_session=db_session,
        max_tokens=800,
    )

    if not result or not isinstance(result, dict):
        logger.warning("Platform analysis failed for %s", senator["name"])
        return empty

    promises_raw = result.get("campaignPromises") or []
    promises = []
    valid_alignments = {"kept", "broken", "partial", "unclear"}
    valid_categories = set(PLATFORM_CATEGORIES.keys())
    for p in promises_raw:
        if not isinstance(p, dict) or not p.get("promiseText"):
            continue

        # Handle category — LLM sometimes returns list instead of string
        category_raw = p.get("category", "other")
        if isinstance(category_raw, list):
            category = category_raw[0] if category_raw else "other"
        else:
            category = category_raw
        category = category if category in valid_categories else "other"

        # Handle alignment — same defensive logic
        alignment_raw = p.get("alignment", "unclear")
        if isinstance(alignment_raw, list):
            alignment = alignment_raw[0] if alignment_raw else "unclear"
        else:
            alignment = alignment_raw
        alignment = alignment if alignment in valid_alignments else "unclear"

        # Extract related bill IDs
        related_bills = p.get("relatedBills", [])
        if not isinstance(related_bills, list):
            related_bills = []
        related_bills = [str(b) for b in related_bills if b][:5]  # Max 5

        promises.append({
            "promiseText": str(p.get("promiseText", ""))[:250],
            "category": category,
            "alignment": alignment,
            "relatedVotes": related_bills,  # Store bill IDs, not full vote objects
            "analysis": str(p.get("analysis", ""))[:400],
        })

    return {
        "senatorId": senator["id"],
        "platformSummary": str(result.get("platformSummary", ""))[:500],
        "campaignPromises": promises,
    }
