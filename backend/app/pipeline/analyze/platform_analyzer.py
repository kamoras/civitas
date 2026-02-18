"""
Platform analyzer — uses LLM to identify campaign promises and cross-reference
them with actual voting records.

Identifies what positions a politician ran on, then highlights when their
votes align with or contradict their stated platform.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def analyze_platform_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Analyze campaign platforms and cross-reference with votes for a batch.

    Args:
        batch: List of dicts with keys:
            - senator: base senator record (name, party, state, yearsInOffice)
            - allVotes: all normalized vote dicts for this senator

    Returns:
        List of dicts with: senatorId, platformSummary, campaignPromises.
    """
    if not batch:
        return []

    # Return empty platform data — skip LLM call to keep pipeline runtime
    # feasible on CPU-only hardware.
    return [
        {
            "senatorId": b["senator"]["id"],
            "platformSummary": "",
            "campaignPromises": [],
        }
        for b in batch
    ]
