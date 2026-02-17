"""
Bill analyzer — classifies bills via LLM. Handles both curated key bills
and dynamically discovered recent roll call votes.
"""

import json
import logging
from typing import Any

from app.pipeline.analyze.ollama_client import call_llm

logger = logging.getLogger(__name__)


INDUSTRY_CODES = (
    "PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, "
    "TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, "
    "LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER"
)


async def classify_all_bills(
    bills: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    Classify ALL bills in a single LLM call for efficiency.

    Args:
        bills: Array of bill objects with text/summary data.
        db_session: SQLAlchemy session for cache access.

    Returns:
        Array of classified bills with partyLeaning, classification, etc.
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
        prompt_version="bill-classify-batch-v4",
        system_prompt=(
            "You are a nonpartisan congressional analyst. Classify bills by their "
            "corporate vs. consumer impact AND by party alignment. Be factual and "
            "balanced. Return ONLY a valid JSON array."
        ),
        user_prompt=f"""Classify each of these {len(bills)} bills. For each, determine:
- classification: "pro-corporate", "pro-consumer", or "mixed"
- proBusinessVote: "Yea" or "Nay" -- which vote position serves corporate/business interests. For bills that increase regulation or taxes on industry, the pro-business vote is "Nay". For bills that provide subsidies, deregulate, or benefit specific industries, the pro-business vote is "Yea". For mixed bills, choose the direction that more strongly favors corporate interests.
- partyLeaning: "R", "D", or "bipartisan" -- which party broadly supports this bill based on its policy direction. Republican-leaning (R): tax cuts, deregulation, defense spending, immigration enforcement, fossil fuel support, law enforcement funding. Democrat-leaning (D): social programs, environmental regulation, healthcare expansion, voting rights, labor protections, gun control. Bipartisan: broad support from both parties (e.g. infrastructure, disaster relief, NDAA, government funding).
- corporateInterest: 1-2 sentences on which industries had a stake
- publicImpact: 1-2 sentences on impact to ordinary people
- description: 1 sentence neutral description
- affectedIndustries: array of codes from [{INDUSTRY_CODES}]

Bills:
{json.dumps(bill_summaries, indent=1)}

Return a JSON array with one object per bill:
[{{"billId": "...", "billName": "...", "congress": ..., "date": "", "description": "...", "proBusinessVote": "Yea|Nay", "partyLeaning": "R|D|bipartisan", "corporateInterest": "...", "publicImpact": "...", "affectedIndustries": [...], "classification": "..."}}]""",
        cache_key={"billIds": [b["billId"] for b in bill_summaries]},
        db_session=db_session,
        max_tokens=8192,
    )

    if not result or not isinstance(result, list):
        logger.error("Batch bill classification failed")
        return []

    _validate_classifications(result)
    logger.info("Classified %d/%d bills", len(result), len(bills))
    return result


async def classify_recent_votes(
    roll_calls: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    Classify recent roll call votes. Lighter classification using vote
    metadata extracted from Senate.gov XML.

    Args:
        roll_calls: Parsed roll call dicts with voteTitle, documentTitle, etc.
        db_session: SQLAlchemy session for cache access.

    Returns:
        Array of classified vote dicts.
    """
    if not roll_calls:
        return []

    logger.info("Classifying %d recent roll call votes...", len(roll_calls))

    # Build summaries from the XML metadata
    vote_summaries = []
    for rc in roll_calls:
        bill_id = rc.get("documentName") or f"Roll-{rc['rollNumber']}"
        name = rc.get("documentTitle") or rc.get("voteTitle") or "Unknown"
        vote_summaries.append({
            "billId": bill_id,
            "billName": name,
            "congress": rc.get("congress", 119),
            "voteDate": rc.get("voteDate", ""),
            "question": rc.get("question", ""),
        })

    result = await call_llm(
        prompt_version="recent-vote-classify-v1",
        system_prompt=(
            "You are a nonpartisan congressional analyst. Classify Senate votes "
            "by their corporate vs. consumer impact and party alignment. Be factual. "
            "Return ONLY a valid JSON array."
        ),
        user_prompt=f"""Classify each of these {len(vote_summaries)} Senate votes:

{json.dumps(vote_summaries, indent=1)}

For each vote, return:
- billId: the bill/resolution ID from the input
- billName: the name from the input
- date: the vote date from the input
- description: 1 sentence neutral description of what was being voted on
- classification: "pro-corporate", "pro-consumer", or "mixed"
- proBusinessVote: "Yea" or "Nay" -- which vote direction serves corporate interests
- partyLeaning: "R", "D", or "bipartisan" -- which party broadly supports this
- corporateInterest: 1 sentence on corporate stakes (or "Minimal direct corporate impact" if none)
- publicImpact: 1 sentence on public impact
- affectedIndustries: array from [{INDUSTRY_CODES}]

Return a JSON array:
[{{"billId": "...", "billName": "...", "date": "...", "description": "...", "classification": "...", "proBusinessVote": "Yea|Nay", "partyLeaning": "R|D|bipartisan", "corporateInterest": "...", "publicImpact": "...", "affectedIndustries": [...]}}]""",
        cache_key={
            "recentVoteIds": [v["billId"] for v in vote_summaries],
        },
        db_session=db_session,
        max_tokens=8192,
    )

    if not result or not isinstance(result, list):
        logger.error("Recent vote classification failed")
        return []

    _validate_classifications(result)
    logger.info("Classified %d/%d recent votes", len(result), len(roll_calls))
    return result


def _validate_classifications(bills: list[dict]) -> None:
    """Validate and fix classification fields in-place."""
    for bill in bills:
        if bill.get("classification") not in ("pro-corporate", "pro-consumer", "mixed"):
            bill["classification"] = "mixed"
        if bill.get("proBusinessVote") not in ("Yea", "Nay"):
            bill["proBusinessVote"] = "Yea"
        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
