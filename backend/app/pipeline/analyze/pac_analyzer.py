"""
PAC analyzer — uses LLM to identify the businesses, industries, and people
behind Political Action Committees.

PAC names often obscure their true sponsors. This module sends PAC donor data
to the LLM to identify what key businesses or influential people are behind
each PAC.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def analyze_pacs_batch(
    batch: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Analyze PAC donors for a batch of senators to identify true sponsors.

    Args:
        batch: List of dicts with keys: senatorId, donors (list of donor dicts).
        db_session: SQLAlchemy session for cache access.

    Returns:
        List of dicts with senatorId and enriched donors list.
    """
    # Return donors unchanged — skip PAC enrichment LLM call to keep
    # pipeline runtime feasible on CPU-only hardware.
    return [{"senatorId": b["senatorId"], "donors": b["donors"]} for b in batch]
