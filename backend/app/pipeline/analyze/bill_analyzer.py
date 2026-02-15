"""
Bill analyzer — classifies all bills in a single LLM call to conserve quota.
"""

import json
import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


async def classify_all_bills(
    bills: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    Classify ALL bills in a single LLM call to conserve quota.

    Args:
        bills: Array of bill objects with text/summary data.
        db_session: SQLAlchemy session for cache access.

    Returns:
        Array of classified bills.
    """
    if len(bills) == 0:
        return []

    logger.info("Classifying %d bills in a single batch...", len(bills))

    bill_summaries = [
        {
            "billId": b["billId"],
            "billName": b["billName"],
            "congress": b["congress"],
            "summary": (b.get("summary") or "")[:500],
        }
        for b in bills
    ]

    result = await call_llm(
        prompt_version="bill-classify-batch-v3",
        system_prompt=(
            "You are a nonpartisan congressional analyst. Classify bills by their "
            "corporate vs. consumer impact. Be factual and balanced. Return ONLY a "
            "valid JSON array."
        ),
        user_prompt=f"""Classify each of these {len(bills)} bills. For each, determine:
- classification: "pro-corporate", "pro-consumer", or "mixed"
- proBusinessVote: "Yea" or "Nay" \u2014 which vote position on this bill serves corporate/business interests. For a bill that increases regulation or taxes on industry, the pro-business vote is "Nay". For a bill that provides subsidies, deregulates, or benefits specific industries, the pro-business vote is "Yea". For mixed bills, choose the vote that more strongly favors corporate interests overall.
- corporateInterest: 1-2 sentences on which industries had a stake
- publicImpact: 1-2 sentences on impact to ordinary people
- description: 1 sentence neutral description
- affectedIndustries: array of codes from [PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER]

Bills:
{json.dumps(bill_summaries, indent=1)}

Return a JSON array with one object per bill:
[{{"billId": "...", "billName": "...", "congress": ..., "date": "", "description": "...", "proBusinessVote": "Yea|Nay", "corporateInterest": "...", "publicImpact": "...", "affectedIndustries": [...], "classification": "..."}}]""",
        cache_key={"billIds": [b["billId"] for b in bill_summaries]},
        db_session=db_session,
        max_tokens=8192,
    )

    if not result or not isinstance(result, list):
        logger.error("Batch bill classification failed")
        return []

    # Validate classifications and proBusinessVote
    for bill in result:
        if bill.get("classification") not in ("pro-corporate", "pro-consumer", "mixed"):
            bill["classification"] = "mixed"
        if bill.get("proBusinessVote") not in ("Yea", "Nay"):
            bill["proBusinessVote"] = "Yea"

    logger.info("Classified %d/%d bills", len(result), len(bills))
    return result
