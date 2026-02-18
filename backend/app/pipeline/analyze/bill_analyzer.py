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
    Classify bills one at a time — gemma2:2b can't reliably handle batches.

    Args:
        bills: Array of bill objects with text/summary data.
        db_session: SQLAlchemy session for cache access.

    Returns:
        Array of classified bills with partyLeaning, classification, etc.
    """
    if len(bills) == 0:
        return []

    logger.info("Classifying %d bills individually...", len(bills))
    classified = []

    for b in bills:
        summary = (b.get("summary") or b.get("billName", ""))[:300]
        result = await call_llm(
            prompt_version="bill-classify-v6",
            system_prompt="Congressional analyst. Return ONLY valid JSON.",
            user_prompt=f"""Classify this bill:
billId: {b['billId']}
billName: {b['billName']}
congress: {b['congress']}
summary: {summary}

Return JSON with EXACTLY these values (pick ONE option for each):
{{"billId":"{b['billId']}","billName":"{b['billName']}","congress":{b['congress']},"date":"","description":"<1 sentence>","proBusinessVote":"Yea","partyLeaning":"R","corporateInterest":"<1 sentence>","publicImpact":"<1 sentence>","affectedIndustries":["<one code from: {INDUSTRY_CODES}>"],"classification":"pro-corporate"}}

Rules:
- classification: exactly one of pro-corporate, pro-consumer, or mixed
- proBusinessVote: exactly "Yea" if voting YES helps corporate interests, or "Nay" if voting NO helps corporate interests
- partyLeaning: exactly R, D, or bipartisan
- affectedIndustries: list 1-3 industry codes from the allowed list""",
            cache_key={"billId": b["billId"], "v": 6},
            db_session=db_session,
            max_tokens=300,
        )

        if result and isinstance(result, dict) and result.get("billId"):
            classified.append(result)
        else:
            logger.warning("Bill classification failed for %s", b["billId"])

    _validate_classifications(classified)
    logger.info("Classified %d/%d bills", len(classified), len(bills))
    return classified


async def classify_recent_votes(
    roll_calls: list[dict], db_session: Any | None = None
) -> list[dict]:
    """
    Classify recent roll call votes one at a time.

    Args:
        roll_calls: Parsed roll call dicts with voteTitle, documentTitle, etc.
        db_session: SQLAlchemy session for cache access.

    Returns:
        Array of classified vote dicts.
    """
    if not roll_calls:
        return []

    logger.info("Classifying %d recent roll call votes individually...", len(roll_calls))
    classified = []

    for rc in roll_calls:
        bill_id = rc.get("documentName") or f"Roll-{rc['rollNumber']}"
        name = rc.get("documentTitle") or rc.get("voteTitle") or "Unknown"
        question = (rc.get("question") or "")[:200]
        vote_date = rc.get("voteDate", "")

        result = await call_llm(
            prompt_version="recent-vote-classify-v3",
            system_prompt="Congressional analyst. Return ONLY valid JSON.",
            user_prompt=f"""Classify this Senate vote:
billId: {bill_id}
billName: {name}
date: {vote_date}
question: {question}

Return JSON with EXACTLY these values (pick ONE option for each):
{{"billId":"{bill_id}","billName":"{name}","date":"{vote_date}","description":"<1 sentence>","classification":"pro-corporate","proBusinessVote":"Yea","partyLeaning":"R","corporateInterest":"<1 sentence>","publicImpact":"<1 sentence>","affectedIndustries":["<one code from: {INDUSTRY_CODES}>"]}}

Rules:
- classification: exactly one of pro-corporate, pro-consumer, or mixed
- proBusinessVote: exactly "Yea" if voting YES helps corporate interests, or "Nay" if voting NO helps corporate interests
- partyLeaning: exactly R, D, or bipartisan
- If this is a presidential nomination (PN prefix) or procedural vote, use classification "mixed" and omit from corporate scoring""",
            cache_key={"billId": bill_id, "v": 3},
            db_session=db_session,
            max_tokens=300,
        )

        if result and isinstance(result, dict) and result.get("billId"):
            classified.append(result)
        else:
            logger.warning("Recent vote classification failed for %s", bill_id)

    _validate_classifications(classified)
    logger.info("Classified %d/%d recent votes", len(classified), len(roll_calls))
    return classified


def _validate_classifications(bills: list[dict]) -> None:
    """Validate and fix classification fields in-place."""
    for bill in bills:
        if bill.get("classification") not in ("pro-corporate", "pro-consumer", "mixed"):
            bill["classification"] = "mixed"
        if bill.get("proBusinessVote") not in ("Yea", "Nay"):
            # Don't guess — set to None so normalize_votes skips this bill
            # for corporate alignment scoring rather than defaulting everyone to 100%
            bill["proBusinessVote"] = None
        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
