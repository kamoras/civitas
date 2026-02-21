"""
Hybrid donor classifier — tiered strategy for donor type and industry.

Classification tiers (donor TYPE):
1. FEC committee type codes (structured data from the API itself)
2. Deterministic pattern rules (payment processors, party committees)
3. Learning store lookup
4. LLM only for truly ambiguous cases

Classification tiers (donor INDUSTRY):
1. Learning store
2. Embedding cosine similarity (from industry_classifier)
3. LLM batch for remaining unknowns

Design rationale: FEC already encodes committee type in structured fields
(committee_type, designation). Using those is faster and more accurate
than asking an LLM to re-derive what the FEC already knows. The LLM
is reserved for the ~5-10% of donors that can't be classified otherwise.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import LearnedClassification
from app.pipeline.analyze.ollama_client import call_llm
from app.pipeline.transform.industry_classifier import (
    classify_industry,
    store_llm_classifications,
)

logger = logging.getLogger(__name__)

# FEC committee type codes → donor types
# See: https://www.fec.gov/campaign-finance-data/committee-type-code-descriptions/
FEC_TYPE_MAP = {
    "N": "PAC",              # PAC - nonqualified
    "Q": "PAC",              # PAC - qualified
    "W": "PAC",              # PAC with non-contribution account
    "O": "PAC",              # Super PAC (independent expenditure only)
    "U": "PAC",              # Single candidate independent expenditure
    "V": "PAC",              # PAC with non-contribution account - nonqualified
    "X": "Party/Ideological",  # Party - nonqualified
    "Y": "Party/Ideological",  # Party - qualified
    "Z": "Party/Ideological",  # National party nonfederal account
    "H": "CandidateAffiliated",  # House candidate
    "S": "CandidateAffiliated",  # Senate candidate
    "P": "CandidateAffiliated",  # Presidential candidate
    "D": "CandidateAffiliated",  # Leadership PAC (delegate)
    "E": "CandidateAffiliated",  # Communication cost maker
    "C": "CandidateAffiliated",  # Communication cost maker
    "I": "Org/Employees",       # Independent expenditor (person)
}

SKIP_PATTERNS = [
    "WINRED", "ACTBLUE", "ANEDOT",
    "VICTORY COMMITTEE", "VICTORY FUND", "JOINT FUNDRAISING",
    "INFORMATION REQUESTED",
]

PARTY_PATTERNS = [
    "DEMOCRATIC NATIONAL COMMITTEE", "REPUBLICAN NATIONAL COMMITTEE",
    "DEMOCRATIC SENATORIAL CAMPAIGN", "DSCC",
    "NATIONAL REPUBLICAN SENATORIAL", "NRSC",
    "DEMOCRATIC CONGRESSIONAL CAMPAIGN", "DCCC",
    "NATIONAL REPUBLICAN CONGRESSIONAL", "NRCC",
    "EMILY'S LIST", "EMILYS LIST", "CLUB FOR GROWTH",
    "MOVEON", "PRIORITIES USA", "SENATE MAJORITY PAC",
    "SENATE LEADERSHIP FUND", "HOUSE MAJORITY PAC",
    "CONGRESSIONAL LEADERSHIP FUND", "AMERICAN CROSSROADS",
    "END CITIZENS UNITED",
]

LLM_CHUNK_SIZE = 20
INDUSTRY_CODES_STR = (
    "FINANCE, HEALTHCARE, TECH, ENERGY, OIL_GAS, DEFENSE, PHARMA, INSURANCE, "
    "REAL_ESTATE, TELECOM, AGRIBUSINESS, CONSTRUCTION, TRANSPORT, LAWYERS, "
    "LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, LABOR_UNIONS, "
    "EDUCATION, MEDIA, RETAIL, MANUFACTURING, HEALTHCARE, POLITICAL, OTHER"
)


def classify_donor_type_from_fec(receipt: dict) -> str | None:
    """Classify donor type using FEC structured metadata.

    FEC receipts include committee_type and designation codes that
    directly encode what kind of entity made the contribution.
    This is the most reliable classification source.
    """
    committee = receipt.get("committee") or {}
    committee_type = committee.get("committee_type", "")
    if committee_type and committee_type in FEC_TYPE_MAP:
        return FEC_TYPE_MAP[committee_type]
    return None


def classify_donor_type_from_rules(name_upper: str) -> str | None:
    """Classify donor type using deterministic pattern rules.

    For well-known entities (payment processors, party committees),
    pattern matching is faster and more reliable than LLM.
    """
    if any(p in name_upper for p in SKIP_PATTERNS):
        return "SKIP"

    if any(p in name_upper for p in PARTY_PATTERNS):
        return "Party/Ideological"

    return None


async def classify_donors_hybrid(
    donors: list[dict],
    db_session: Session | None = None,
) -> dict[str, dict]:
    """Classify donors using the full tiered strategy.

    Args:
        donors: List of dicts with 'name' and optionally 'amount', 'fec_receipt'.
        db_session: SQLAlchemy session for learning store access.

    Returns:
        Dict mapping UPPERCASE donor name -> {type, industry, skip}
    """
    if not donors:
        return {}

    seen: set[str] = set()
    unique_donors: list[dict] = []
    for d in donors:
        key = (d.get("name") or "").upper().strip()
        if key and key != "UNKNOWN" and key not in seen:
            seen.add(key)
            unique_donors.append(d)

    if not unique_donors:
        return {}

    results: dict[str, dict] = {}
    needs_llm: list[dict] = []

    known_from_db: dict[str, dict] = {}
    if db_session is not None:
        unique_names = [d["name"].upper().strip() for d in unique_donors]
        rows = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name.in_(unique_names),
                LearnedClassification.entity_type.in_(["donor_type", "industry"]),
            )
            .all()
        )
        for r in rows:
            if r.entity_name not in known_from_db:
                known_from_db[r.entity_name] = {}
            known_from_db[r.entity_name][r.entity_type] = r.value

    tier_stats = {"fec": 0, "rules": 0, "learned": 0, "embedding": 0, "llm_needed": 0}

    for donor in unique_donors:
        name = donor["name"]
        name_upper = name.upper().strip()
        fec_receipt = donor.get("fec_receipt", {})

        donor_type = None
        industry = None
        source_type = None

        learned = known_from_db.get(name_upper, {})
        if "donor_type" in learned and "industry" in learned:
            donor_type = learned["donor_type"]
            industry = learned["industry"]
            source_type = "learned"
            tier_stats["learned"] += 1
        else:
            fec_type = classify_donor_type_from_fec(fec_receipt) if fec_receipt else None
            if fec_type:
                donor_type = fec_type
                source_type = "fec"
                tier_stats["fec"] += 1
            else:
                rule_type = classify_donor_type_from_rules(name_upper)
                if rule_type:
                    donor_type = rule_type
                    source_type = "rules"
                    tier_stats["rules"] += 1

            if industry is None:
                industry = classify_industry(name)
                if industry != "OTHER":
                    tier_stats["embedding"] += 1

        if donor_type and industry and industry != "OTHER":
            results[name_upper] = {
                "type": donor_type,
                "industry": industry,
                "skip": donor_type == "SKIP",
            }
            if db_session is not None and source_type != "learned":
                _store_donor_learning(db_session, name_upper, donor_type, industry, source_type or "embedding")
        elif donor_type:
            results[name_upper] = {
                "type": donor_type,
                "industry": industry or "OTHER",
                "skip": donor_type == "SKIP",
            }
            if industry == "OTHER":
                needs_llm.append(donor)
            if db_session is not None and source_type and source_type != "learned":
                _store_donor_learning(db_session, name_upper, donor_type, industry or "OTHER", source_type)
        else:
            needs_llm.append(donor)
            tier_stats["llm_needed"] += 1

    logger.info(
        "Donor classification tiers: %d FEC, %d rules, %d learned, %d embedding, %d need LLM (of %d total)",
        tier_stats["fec"], tier_stats["rules"], tier_stats["learned"],
        tier_stats["embedding"], tier_stats["llm_needed"], len(unique_donors),
    )

    if needs_llm:
        llm_results = await _classify_remaining_via_llm(needs_llm, db_session)
        for name_upper, classification in llm_results.items():
            existing = results.get(name_upper, {})
            merged = {**existing, **classification}
            merged["skip"] = merged.get("type") == "SKIP"
            results[name_upper] = merged

    if db_session is not None:
        db_session.flush()

    return results


async def _classify_remaining_via_llm(
    donors: list[dict],
    db_session: Session | None,
) -> dict[str, dict]:
    """LLM fallback for donors that couldn't be classified by faster methods."""
    if not donors:
        return {}

    logger.info("LLM classifying %d remaining donors...", len(donors))
    all_results: dict[str, dict] = {}
    industry_learnings: dict[str, str] = {}

    for i in range(0, len(donors), LLM_CHUNK_SIZE):
        chunk = donors[i : i + LLM_CHUNK_SIZE]
        donor_lines = "\n".join(
            f"- {d['name']} (${d.get('amount', 0):,.0f})" for d in chunk
        )

        result = await call_llm(
            prompt_version="donor-classify-v3",
            system_prompt="Campaign finance analyst. Return ONLY valid JSON array.",
            user_prompt=(
                f"Classify these political donors by type and industry.\n\n"
                f"DONORS:\n{donor_lines}\n\n"
                f"TYPE: PAC | Org/Employees | Party/Ideological | CandidateAffiliated | SKIP\n"
                f"INDUSTRY: {INDUSTRY_CODES_STR}\n\n"
                f'Return JSON: [{{"name":"<name>","type":"<type>","industry":"<code>"}}]'
            ),
            cache_key={
                "donors": sorted(d["name"].upper().strip() for d in chunk),
                "v": 3,
            },
            db_session=db_session,
            max_tokens=1200,
        )

        if result and isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").upper().strip()
                if not name:
                    continue
                dtype = item.get("type", "PAC")
                ind = item.get("industry", "OTHER")
                all_results[name] = {"type": dtype, "industry": ind}
                if ind != "OTHER":
                    industry_learnings[name] = ind
                if db_session is not None:
                    _store_donor_learning(db_session, name, dtype, ind, "llm")

    if industry_learnings and db_session is not None:
        store_llm_classifications(industry_learnings, db_session)

    return all_results


def _store_donor_learning(
    db_session: Session,
    name_upper: str,
    donor_type: str,
    industry: str,
    source: str,
) -> None:
    """Store both type and industry classifications in the learning store."""
    from datetime import datetime

    confidence = {"fec": 1.0, "rules": 0.95, "embedding": 0.9, "llm": 0.7}.get(source, 0.5)

    for entity_type, value in [("donor_type", donor_type), ("industry", industry)]:
        existing = (
            db_session.query(LearnedClassification)
            .filter(
                LearnedClassification.entity_name == name_upper,
                LearnedClassification.entity_type == entity_type,
            )
            .first()
        )
        if existing:
            if confidence >= existing.confidence:
                existing.value = value
                existing.confidence = confidence
                existing.source = source
                existing.learned_at = datetime.utcnow()
        else:
            db_session.add(LearnedClassification(
                entity_name=name_upper,
                entity_type=entity_type,
                value=value,
                confidence=confidence,
                source=source,
            ))
