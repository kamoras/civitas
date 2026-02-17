"""
Vote summarizer — uses LLM to select key votes and generate voting summary.
"""

import json
import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


async def select_key_votes_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    For a batch of senators, select the most notable votes and generate
    a voting summary narrative. Runs as one LLM call for the batch.

    Args:
        batch: List of dicts with keys: senator (name, party, state, id),
               allVotes (list of vote dicts with billId, billName, vote,
               partyLeaning, votedWithParty, classification).
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

    senator_blocks = []
    for idx, item in enumerate(batch):
        senator = item["senator"]
        votes = item["allVotes"]

        votes_summary = json.dumps(
            [
                {
                    "billId": v["billId"],
                    "billName": v["billName"],
                    "vote": v["vote"],
                    "partyLeaning": v.get("partyLeaning", "bipartisan"),
                    "votedWithParty": v.get("votedWithParty"),
                    "classification": v.get("classification", "mixed"),
                }
                for v in votes
            ],
            indent=1,
        )

        block = (
            f"--- SENATOR {idx + 1}: {senator['name']} "
            f"({senator['party']}-{senator['state']}) ---\n"
            f"ID: {senator['id']}\n"
            f"ALL VOTES ({len(votes)}):\n{votes_summary}"
        )
        senator_blocks.append(block)

    result = await call_llm(
        prompt_version="key-vote-select-v1",
        system_prompt=(
            "You are a nonpartisan political analyst. Select the most notable "
            "and interesting votes for each senator and write a brief narrative "
            "summary of their voting patterns. Focus on: cross-party votes "
            "(voting against their own party), high-profile legislation, and "
            "votes where corporate interests and party loyalty may have conflicted. "
            "Be factual and balanced. Return ONLY valid JSON."
        ),
        user_prompt=f"""For each of the {len(batch)} senators below, select 3-5 of their most notable votes and write a voting summary.

{chr(10).join(senator_blocks)}

For each senator, return:
- senatorId: the senator's ID
- keyVoteIds: array of 3-5 billId strings for the most notable/interesting votes
- reasoning: object mapping each selected billId to a 1-2 sentence explanation of why this vote is notable. Prioritize: (1) votes AGAINST their own party, (2) votes on high-profile or controversial legislation, (3) votes where donor/corporate interests may have been a factor.
- votingSummary: A 2-3 sentence narrative paragraph summarizing this senator's voting patterns, party loyalty, and any notable cross-party tendencies. Include the approximate party loyalty percentage if calculable from the data.

Return a JSON array:
[{{"senatorId": "...", "keyVoteIds": ["..."], "reasoning": {{"billId": "explanation..."}}, "votingSummary": "..."}}]""",
        cache_key={
            "senatorIds": [item["senator"]["id"] for item in batch],
            "voteCounts": [len(item["allVotes"]) for item in batch],
        },
        db_session=db_session,
        max_tokens=min(len(batch) * 2000, 16384),
    )

    if not result or not isinstance(result, list):
        logger.warning("Key vote selection failed for batch of %d", len(batch))
        return [
            {
                "senatorId": item["senator"]["id"],
                "keyVoteIds": [],
                "reasoning": {},
                "votingSummary": "",
            }
            for item in batch
        ]

    # Match results to senators
    results_map = {r.get("senatorId"): r for r in result if r.get("senatorId")}
    output = []
    for i, item in enumerate(batch):
        sid = item["senator"]["id"]
        matched = results_map.get(sid) or (result[i] if i < len(result) else None)
        if matched:
            output.append({
                "senatorId": sid,
                "keyVoteIds": matched.get("keyVoteIds", []),
                "reasoning": matched.get("reasoning", {}),
                "votingSummary": matched.get("votingSummary", ""),
            })
        else:
            output.append({
                "senatorId": sid,
                "keyVoteIds": [],
                "reasoning": {},
                "votingSummary": "",
            })

    return output
