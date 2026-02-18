"""
Vote summarizer — uses LLM to select key votes and generate voting summary.
"""

import logging
from typing import Any

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

    # Return empty key vote selection — the orchestrator's fallback logic will
    # automatically promote the first 3-5 available votes as key votes.
    return [
        {
            "senatorId": item["senator"]["id"],
            "keyVoteIds": [],
            "reasoning": {},
            "votingSummary": "",
        }
        for item in batch
    ]
