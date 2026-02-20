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

# Policy areas and their valid stances
POLICY_TAXONOMY = {
    "LABOR": ["pro-union", "anti-union", "pro-worker", "pro-employer", "neutral"],
    "DEFENSE": ["pro-military", "anti-military", "pro-intervention", "anti-intervention", "neutral"],
    "GUNS": ["pro-gun-rights", "pro-gun-control", "neutral"],
    "HEALTHCARE": ["pro-expansion", "pro-restriction", "pro-pharma", "pro-consumer", "neutral"],
    "ENVIRONMENT": ["pro-regulation", "anti-regulation", "pro-climate", "pro-industry", "neutral"],
    "TAXES": ["pro-corporate-cuts", "pro-individual-cuts", "pro-tax-increases", "neutral"],
    "IMMIGRATION": ["pro-border-security", "pro-reform", "pro-pathway", "neutral"],
    "EDUCATION": ["pro-public-schools", "pro-teachers", "pro-charter", "pro-vouchers", "neutral"],
    "FINANCIAL": ["pro-regulation", "pro-deregulation", "pro-consumer-protection", "neutral"],
    "ENERGY": ["pro-fossil-fuel", "pro-renewable", "pro-nuclear", "neutral"],
    "TECH": ["pro-regulation", "anti-regulation", "pro-privacy", "pro-big-tech", "neutral"],
    "JUSTICE": ["pro-police", "pro-reform", "pro-sentencing-reform", "neutral"],
    "TRADE": ["pro-protectionism", "pro-free-trade", "neutral"],
    "WELFARE": ["pro-expansion", "pro-restriction", "neutral"],
    "PROCEDURAL": ["nomination", "procedural", "bipartisan-consensus"],
}

POLICY_AREAS = ", ".join(POLICY_TAXONOMY.keys())

IMPACTED_GROUPS = [
    "workers", "unions", "employers", "corporations", "consumers", "veterans",
    "seniors", "students", "teachers", "gun-owners", "immigrants", "farmers",
    "small-business", "wall-street", "tech-companies", "fossil-fuel-industry",
    "renewable-energy", "healthcare-industry", "patients", "taxpayers", "wealthy",
    "middle-class", "low-income", "police", "criminal-justice-system"
]


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
            prompt_version="bill-classify-v7",
            system_prompt="Congressional policy analyst. Return ONLY valid JSON.",
            user_prompt=f"""Classify this bill by policy area and stance:
billId: {b['billId']}
billName: {b['billName']}
congress: {b['congress']}
summary: {summary}

Return JSON:
{{"billId":"{b['billId']}","billName":"{b['billName']}","congress":{b['congress']},"date":"","description":"<1 sentence>","policyArea":"<pick ONE from: {POLICY_AREAS}>","stance":"<based on policyArea, pick appropriate stance>","stanceVote":"Yea","impactedGroups":["workers","corporations"],"corporateInterest":"<1 sentence>","publicImpact":"<1 sentence>","affectedIndustries":["<industry codes>"],"partyLeaning":"R"}}

Rules:
- policyArea: pick the single most relevant area from the list above
- stance: based on the policyArea, pick the specific policy position this bill takes (e.g., if LABOR: pro-union, anti-union, pro-worker, pro-employer; if GUNS: pro-gun-rights, pro-gun-control; if HEALTHCARE: pro-expansion, pro-restriction, pro-pharma, pro-consumer; if PROCEDURAL: nomination, procedural, bipartisan-consensus)
- stanceVote: "Yea" if voting YES advances the stance, "Nay" if voting NO advances it
- impactedGroups: list 2-4 groups most affected (workers, unions, employers, corporations, consumers, veterans, seniors, students, teachers, gun-owners, immigrants, farmers, small-business, wall-street, tech-companies, fossil-fuel-industry, renewable-energy, healthcare-industry, patients, taxpayers, wealthy, middle-class, low-income, police)
- partyLeaning: R, D, or bipartisan
- affectedIndustries: list 1-3 industry codes from: {INDUSTRY_CODES}""",
            cache_key={"billId": b["billId"], "v": 7},
            db_session=db_session,
            max_tokens=350,
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
            prompt_version="recent-vote-classify-v4",
            system_prompt="Congressional policy analyst. Return ONLY valid JSON.",
            user_prompt=f"""Classify this Senate vote by policy area and stance:
billId: {bill_id}
billName: {name}
date: {vote_date}
question: {question}

Return JSON:
{{"billId":"{bill_id}","billName":"{name}","date":"{vote_date}","description":"<1 sentence>","policyArea":"<e.g., LABOR, DEFENSE, HEALTHCARE, PROCEDURAL, etc.>","stance":"<specific position, e.g., pro-union, pro-gun-control, pro-expansion, nomination, etc.>","stanceVote":"Yea","impactedGroups":["workers","corporations"],"corporateInterest":"<1 sentence>","publicImpact":"<1 sentence>","affectedIndustries":["<codes>"],"partyLeaning":"D"}}

Rules:
- policyArea: identify the primary policy domain (common areas: LABOR, DEFENSE, GUNS, HEALTHCARE, ENVIRONMENT, TAXES, IMMIGRATION, EDUCATION, FINANCIAL, ENERGY, TECH, JUSTICE, TRADE, WELFARE, PROCEDURAL)
- stance: describe the specific policy position (e.g., pro-union, anti-regulation, pro-climate, pro-gun-rights, nomination, procedural). BE SPECIFIC - avoid generic terms.
- stanceVote: "Yea" if voting YES advances that stance, "Nay" if voting NO advances it
- impactedGroups: list 2-4 affected groups (workers, unions, employers, corporations, consumers, veterans, seniors, students, teachers, gun-owners, immigrants, farmers, small-business, wall-street, tech-companies, fossil-fuel-industry, patients, taxpayers, wealthy, middle-class, low-income, police, etc.)
- For nominations or purely procedural votes: use policyArea "PROCEDURAL" and stance "nomination" or "procedural"
- partyLeaning: R, D, or bipartisan""",
            cache_key={"billId": bill_id, "v": 4},
            db_session=db_session,
            max_tokens=350,
        )

        if result and isinstance(result, dict) and result.get("billId"):
            classified.append(result)
        else:
            logger.warning("Recent vote classification failed for %s", bill_id)

    _validate_classifications(classified)
    logger.info("Classified %d/%d recent votes", len(classified), len(roll_calls))
    return classified


def _validate_classifications(bills: list[dict]) -> None:
    """Validate and fix classification fields in-place.

    Uses lenient validation to allow AI-generated policy areas and stances
    to evolve dynamically without hardcoded constraints.
    """
    for bill in bills:
        # Ensure policyArea exists and is a non-empty string
        if not bill.get("policyArea") or not isinstance(bill.get("policyArea"), str):
            bill["policyArea"] = "PROCEDURAL"
        bill["policyArea"] = bill["policyArea"].strip().upper()

        # Ensure stance exists and is a non-empty string
        if not bill.get("stance") or not isinstance(bill.get("stance"), str):
            bill["stance"] = "neutral"
        bill["stance"] = bill["stance"].strip().lower()

        # Validate stanceVote (must be Yea or Nay)
        if bill.get("stanceVote") not in ("Yea", "Nay"):
            bill["stanceVote"] = None  # None = skip this bill in alignment scoring

        # Ensure impactedGroups is a list
        if not isinstance(bill.get("impactedGroups"), list):
            bill["impactedGroups"] = []

        # Validate partyLeaning
        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
