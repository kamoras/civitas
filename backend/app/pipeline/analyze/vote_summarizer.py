"""
Vote summarizer — uses LLM to select the most notable votes for each senator
and generate a narrative voting summary.

Key votes are selected based on:
- Votes against party line (maverick moments)
- Votes with clear donor/industry alignment
- Controversial or high-profile legislation
- Votes that reveal patterns in the senator's priorities
"""

import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


async def select_key_votes_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    For a batch of senators, select the most notable votes and generate
    a voting summary narrative.

    Args:
        batch: List of dicts with keys: senator (name, party, state, id),
               allVotes (list of vote dicts with billId, billName, vote,
               partyLeaning, votedWithParty, classification, policyArea, stance).
        db_session: SQLAlchemy session for cache access.

    Returns:
        List of dicts (one per senator):
        {
            "senatorId": str,
            "keyVoteIds": [billId, ...],
            "reasoning": {billId: "Why this is notable...", ...},
            "votingSummary": "Narrative paragraph..."
        }
    """
    if not batch:
        return []

    results = []
    for item in batch:
        senator = item["senator"]
        all_votes = item.get("allVotes", [])

        result = await _select_key_votes(senator, all_votes, db_session)
        results.append(result)

    return results


async def _select_key_votes(
    senator: dict,
    all_votes: list[dict],
    db_session: Any | None,
) -> dict:
    """Select key votes and generate voting summary for a single senator."""
    empty = {
        "senatorId": senator["id"],
        "keyVoteIds": [],
        "reasoning": {},
        "votingSummary": "",
    }

    substantive_votes = [
        v for v in all_votes
        if v.get("vote") in ("Yea", "Nay")
        and v.get("policyArea", "PROCEDURAL") != "PROCEDURAL"
    ]

    if len(substantive_votes) < 3:
        return empty

    vote_lines = []
    for v in substantive_votes[:25]:
        party_tag = ""
        if v.get("votedWithParty") is False:
            party_tag = " [AGAINST PARTY]"
        elif v.get("votedWithParty") is True:
            party_tag = " [WITH PARTY]"

        vote_lines.append(
            f"{v['billId']} | {v.get('billName', '')[:80]} | "
            f"Voted: {v['vote']} | {v.get('policyArea', '')} ({v.get('stance', '')})"
            f"{party_tag}"
        )
    votes_text = "\n".join(vote_lines)

    result = await call_llm(
        prompt_version="vote-summary-v1",
        system_prompt="Political analyst. Return ONLY valid JSON.",
        user_prompt=(
            f"Senator {senator['name']} ({senator['party']}-{senator['state']}), "
            f"{senator.get('yearsInOffice', 0)} years in office.\n\n"
            f"VOTES:\n{votes_text}\n\n"
            f"Select the 5-8 MOST NOTABLE votes and explain why. Notable means:\n"
            f"- Votes AGAINST party line (maverick moments)\n"
            f"- Controversial or high-profile legislation\n"
            f"- Votes that show clear policy priorities or patterns\n"
            f"- Votes on major issues (healthcare, defense, economy, environment)\n"
            f"Skip procedural/routine/nomination votes.\n\n"
            f"Return JSON:\n"
            f'{{"keyVoteIds":["<billId>","<billId>",...],'
            f'"reasoning":{{"<billId>":"<1 sentence: why this vote is notable>"}},'
            f'"votingSummary":"<2-3 sentences: what do these votes reveal about '
            f"this senator's priorities and independence?>\"}}"
        ),
        cache_key={
            "senatorId": senator["id"],
            "voteCount": len(substantive_votes),
            "v": 1,
        },
        db_session=db_session,
        max_tokens=600,
    )

    if not result or not isinstance(result, dict):
        logger.warning("Vote summary failed for %s", senator["name"])
        return empty

    key_ids = result.get("keyVoteIds", [])
    if not isinstance(key_ids, list):
        key_ids = []
    valid_bill_ids = {v["billId"] for v in all_votes}
    key_ids = [bid for bid in key_ids if bid in valid_bill_ids]

    reasoning = result.get("reasoning", {})
    if not isinstance(reasoning, dict):
        reasoning = {}

    return {
        "senatorId": senator["id"],
        "keyVoteIds": key_ids[:8],
        "reasoning": {k: str(v)[:300] for k, v in reasoning.items() if k in valid_bill_ids},
        "votingSummary": str(result.get("votingSummary", ""))[:500],
    }
