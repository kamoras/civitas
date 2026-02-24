"""
Bill analyzer — pure embedding-based classification (zero LLM calls).

Policy area and affected industries are classified via cosine similarity
between bill text and pre-computed embeddings.  Stance, impacted groups,
and narrative fields are derived from Congress.gov summaries, keywords,
and policy-area templates.

This approach replaces the prior LLM-based classifier and eliminates
~90% of pipeline LLM calls.  The reasoning-heavy work (key vote
selection, donor-vote connections) is handled downstream by the
cross-reference and platform analysis modules which still use the LLM.
"""

import logging
import re
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


INDUSTRY_CODES = {
    "PHARMA", "INSURANCE", "OIL_GAS", "DEFENSE", "FINANCE", "REAL_ESTATE",
    "TECH", "TELECOM", "AGRIBUSINESS", "ENERGY", "CONSTRUCTION", "TRANSPORT",
    "LAWYERS", "LOBBYISTS", "GAMBLING", "GUNS", "TOBACCO", "CRYPTO",
    "PRIVATE_PRISON", "OTHER",
}

POLICY_TAXONOMY = {
    "LABOR": "labor unions workers employment wages collective bargaining NLRB workforce rights minimum wage overtime",
    "DEFENSE": "military defense national security armed forces Pentagon troops veterans weapons procurement defense spending authorization",
    "GUNS": "firearms gun control second amendment weapons background checks ammunition assault weapons mass shootings",
    "HEALTHCARE": "healthcare medical insurance hospitals Medicare Medicaid public health drugs prescription ACA Affordable Care Act",
    "ENVIRONMENT": "environment climate change pollution EPA emissions regulations conservation clean air water endangered species carbon",
    "TAXES": "taxes tax reform revenue IRS deductions credits corporate tax income tax estate tax capital gains",
    "IMMIGRATION": "immigration border security asylum refugees visa DACA citizenship deportation undocumented",
    "EDUCATION": "education schools universities student loans teachers curriculum STEM federal education Pell grants Title I",
    "FINANCIAL": "financial regulation banking Wall Street SEC consumer protection Dodd-Frank CFPB banking oversight",
    "ENERGY": "energy renewable solar wind nuclear fossil fuel pipeline drilling electricity clean energy subsidies grid power plant",
    "TECH": "technology internet data privacy cybersecurity artificial intelligence social media antitrust big tech surveillance",
    "JUSTICE": "criminal justice police reform sentencing prison courts judicial system law enforcement bail mandatory minimum",
    "TRADE": "trade tariffs imports exports international commerce USMCA sanctions foreign trade China",
    "WELFARE": "welfare social programs food stamps SNAP housing assistance unemployment benefits poverty safety net",
    "PROCEDURAL": "nomination confirmation cloture procedural motion table resolution appointing executive calendar",
}

POLICY_AREAS = ", ".join(POLICY_TAXONOMY.keys())

PROCEDURAL_KEYWORDS = [
    "nomination", "confirming", "cloture", "motion to proceed",
    "motion to table", "motion to reconsider", "quorum",
    "adjourn", "reading of the journal", "appointment",
    "resolution of ratification", "executive calendar",
    "providing for congressional disapproval",
    "a bill to provide for the appointment of",
    "naming of room", "naming of building",
    "commemorating", "honoring the life",
    "designating the week", "designating the month",
    "electing a member", "relative to the death",
    "fixing the daily hour", "authorizing the use of the rotunda",
    "waiving a requirement", "making technical corrections",
    "en bloc", "sine die",
]

POLICY_IMPACTED_GROUPS: dict[str, list[str]] = {
    "LABOR": ["workers", "unions", "employers", "small businesses"],
    "DEFENSE": ["military personnel", "defense contractors", "veterans", "taxpayers"],
    "GUNS": ["gun owners", "public safety advocates", "firearms industry", "law enforcement"],
    "HEALTHCARE": ["patients", "healthcare workers", "insurance industry", "hospitals"],
    "ENVIRONMENT": ["local communities", "energy companies", "farmers", "wildlife"],
    "TAXES": ["taxpayers", "businesses", "low-income households", "investors"],
    "IMMIGRATION": ["immigrants", "border communities", "employers", "asylum seekers"],
    "EDUCATION": ["students", "teachers", "universities", "families"],
    "FINANCIAL": ["consumers", "banks", "investors", "small businesses"],
    "ENERGY": ["energy consumers", "utility companies", "renewable energy sector", "fossil fuel workers"],
    "TECH": ["tech companies", "consumers", "privacy advocates", "small businesses"],
    "JUSTICE": ["incarcerated individuals", "law enforcement", "communities of color", "courts"],
    "TRADE": ["domestic manufacturers", "consumers", "exporters", "agricultural sector"],
    "WELFARE": ["low-income families", "social workers", "taxpayers", "homeless individuals"],
    "PROCEDURAL": [],
}

_ACTION_PATTERNS: list[tuple[list[str], str]] = [
    (["ban", "prohibit", "restrict", "limit", "block"], "restrict {area} activities"),
    (["protect", "strengthen", "expand", "extend", "increase"], "strengthen {area} protections"),
    (["repeal", "eliminate", "remove", "defund", "rescind"], "roll back {area} measures"),
    (["reform", "modernize", "update", "overhaul"], "reform {area} policy"),
    (["fund", "appropriate", "authorize spending", "invest"], "fund {area} programs"),
    (["establish", "create", "institute"], "establish new {area} measures"),
    (["require", "mandate"], "mandate {area} requirements"),
    (["reauthorize"], "reauthorize {area} programs"),
]

_policy_embeddings: dict[str, np.ndarray] = {}
_industry_embeddings: dict[str, np.ndarray] = {}


def _get_policy_embeddings() -> dict[str, np.ndarray]:
    """Pre-compute embeddings for each policy area description."""
    if _policy_embeddings:
        return _policy_embeddings

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    for area, description in POLICY_TAXONOMY.items():
        emb = model.encode([description], show_progress_bar=False)[0]
        _policy_embeddings[area] = emb / np.linalg.norm(emb)

    return _policy_embeddings


def _get_industry_embeddings() -> dict[str, np.ndarray]:
    """Pre-compute embeddings for industry descriptions (for bill→industry matching)."""
    if _industry_embeddings:
        return _industry_embeddings

    from app.pipeline.transform.industry_classifier import INDUSTRY_DESCRIPTIONS
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    for industry, description in INDUSTRY_DESCRIPTIONS.items():
        if industry not in INDUSTRY_CODES:
            continue
        emb = model.encode([description], show_progress_bar=False)[0]
        _industry_embeddings[industry] = emb / np.linalg.norm(emb)

    return _industry_embeddings


# ── Policy area classification (embeddings) ──────────────────────


def classify_policy_area(text: str) -> tuple[str, float]:
    """Classify policy area via embedding cosine similarity."""
    if not text or len(text.strip()) < 5:
        return "PROCEDURAL", 0.0

    lower = text.lower()
    for kw in PROCEDURAL_KEYWORDS:
        if kw in lower:
            return "PROCEDURAL", 1.0

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    policy_embs = _get_policy_embeddings()

    query_emb = model.encode([text[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_area = "PROCEDURAL"
    best_score = 0.0

    for area, area_emb in policy_embs.items():
        score = float(np.dot(query_emb, area_emb))
        if score > best_score:
            best_score = score
            best_area = area

    return best_area, best_score


# ── Industry classification for bills (embeddings) ──────────────


INDUSTRY_SIMILARITY_THRESHOLD = 0.32


def classify_affected_industries(text: str, top_n: int = 3) -> list[str]:
    """Classify which industries a bill affects using embedding similarity."""
    if not text or len(text.strip()) < 10:
        return []

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    industry_embs = _get_industry_embeddings()

    query_emb = model.encode([text[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    scores = []
    for industry, ind_emb in industry_embs.items():
        score = float(np.dot(query_emb, ind_emb))
        if score > INDUSTRY_SIMILARITY_THRESHOLD:
            scores.append((industry, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [industry for industry, _ in scores[:top_n]]


# ── Stance and narrative derivation (keyword + template) ─────────


def derive_stance(bill_name: str, summary: str, policy_area: str) -> str:
    """Derive a brief stance description from bill name and summary."""
    if summary and len(summary) > 30:
        first_sentence = summary.split(".")[0].strip()
        first_sentence = re.sub(r"<[^>]+>", "", first_sentence).strip()
        if len(first_sentence) > 20:
            return first_sentence[:150]

    text = bill_name.lower()
    area = policy_area.lower().replace("_", " ")

    for keywords, template in _ACTION_PATTERNS:
        if any(w in text for w in keywords):
            return template.format(area=area)

    return f"{area} legislation"


def _make_corporate_interest(policy_area: str, industries: list[str]) -> str:
    if not industries:
        return f"May affect businesses in the {policy_area.lower().replace('_', ' ')} sector"
    names = [i.lower().replace("_", " ") for i in industries[:2]]
    return f"Relevant to {' and '.join(names)} industry interests"


def _make_public_impact(policy_area: str, groups: list[str]) -> str:
    if not groups:
        return f"Affects the general public through {policy_area.lower().replace('_', ' ')} policy"
    return f"Impacts {', '.join(groups[:2])}"


# ── Main classification functions ────────────────────────────────


async def classify_all_bills(
    bills: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify bills using embeddings and templates (zero LLM calls).

    Policy area and affected industries use cosine similarity.
    Stance, impacted groups, and descriptions come from Congress.gov
    summaries and keyword/template derivation.
    """
    if not bills:
        return []

    logger.info("Classifying %d bills (embedding-based, zero LLM)...", len(bills))
    classified = []
    procedural_count = 0

    for b in bills:
        summary = (b.get("summary") or b.get("billName", ""))[:300]
        bill_text = f"{b['billName']} {summary}"

        policy_area, confidence = classify_policy_area(bill_text)

        if policy_area == "PROCEDURAL" and confidence >= 0.8:
            classified.append(_make_procedural(b))
            procedural_count += 1
        else:
            industries = classify_affected_industries(bill_text)
            stance = derive_stance(b["billName"], summary, policy_area)
            groups = POLICY_IMPACTED_GROUPS.get(policy_area, ["general public"])[:3]
            description = _clean_summary(summary, b["billName"])

            classified.append({
                "billId": b["billId"],
                "billName": b["billName"],
                "congress": b["congress"],
                "date": "",
                "description": description,
                "policyArea": policy_area,
                "stance": stance,
                "stanceVote": "Yea",
                "impactedGroups": groups,
                "corporateInterest": _make_corporate_interest(policy_area, industries),
                "publicImpact": _make_public_impact(policy_area, groups),
                "affectedIndustries": industries,
                "partyLeaning": "bipartisan",
            })

    _validate_classifications(classified)
    logger.info(
        "Classified %d/%d bills (%d procedural, zero LLM calls)",
        len(classified), len(bills), procedural_count,
    )
    return classified


async def classify_recent_votes(
    roll_calls: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify recent roll call votes using embeddings (zero LLM calls)."""
    if not roll_calls:
        return []

    logger.info("Classifying %d recent votes (embedding-based, zero LLM)...", len(roll_calls))
    classified = []
    procedural_count = 0

    for rc in roll_calls:
        bill_id = (
            rc.get("documentName")
            or f"Roll-{rc.get('congress', '')}-{rc.get('session', '')}-{rc['rollNumber']}"
        )
        name = rc.get("documentTitle") or rc.get("voteTitle") or "Unknown"
        question = (rc.get("question") or "")[:200]
        vote_date = rc.get("voteDate", "")

        description = question if question and question != name else name

        vote_text = f"{name} {question}"
        policy_area, confidence = classify_policy_area(vote_text)

        if policy_area == "PROCEDURAL" and confidence >= 0.8:
            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "stance": "nomination" if "nominat" in vote_text.lower() else "procedural",
                "stanceVote": None,
                "impactedGroups": [],
                "corporateInterest": "",
                "publicImpact": "",
                "affectedIndustries": [],
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
        else:
            industries = classify_affected_industries(vote_text)
            stance = derive_stance(name, question, policy_area)
            groups = POLICY_IMPACTED_GROUPS.get(policy_area, ["general public"])[:3]

            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": policy_area,
                "stance": stance,
                "stanceVote": "Yea",
                "impactedGroups": groups,
                "corporateInterest": _make_corporate_interest(policy_area, industries),
                "publicImpact": _make_public_impact(policy_area, groups),
                "affectedIndustries": industries,
                "partyLeaning": "bipartisan",
            })

    _validate_classifications(classified)
    logger.info(
        "Classified %d/%d recent votes (%d procedural, zero LLM calls)",
        len(classified), len(roll_calls), procedural_count,
    )
    return classified


# ── Helpers ──────────────────────────────────────────────────────


def _make_procedural(b: dict) -> dict:
    return {
        "billId": b["billId"],
        "billName": b["billName"],
        "congress": b.get("congress", 0),
        "date": "",
        "description": b["billName"],
        "policyArea": "PROCEDURAL",
        "stance": "procedural",
        "stanceVote": None,
        "impactedGroups": [],
        "corporateInterest": "",
        "publicImpact": "",
        "affectedIndustries": [],
        "partyLeaning": "bipartisan",
    }


def _clean_summary(summary: str, fallback: str) -> str:
    """Extract clean description from Congress.gov summary HTML."""
    if not summary or len(summary.strip()) < 10:
        return fallback
    clean = re.sub(r"<[^>]+>", "", summary).strip()
    if len(clean) > 200:
        cut = clean[:200].rsplit(" ", 1)[0]
        return cut + "..."
    return clean or fallback


def _validate_classifications(bills: list[dict]) -> None:
    """Validate and fix classification fields in-place."""
    for bill in bills:
        if not bill.get("policyArea") or not isinstance(bill.get("policyArea"), str):
            bill["policyArea"] = "PROCEDURAL"
        bill["policyArea"] = bill["policyArea"].strip().upper()

        if not bill.get("stance") or not isinstance(bill.get("stance"), str):
            bill["stance"] = "neutral"
        bill["stance"] = bill["stance"].strip().lower()

        if bill.get("stanceVote") not in ("Yea", "Nay"):
            bill["stanceVote"] = None

        if not isinstance(bill.get("impactedGroups"), list):
            bill["impactedGroups"] = []

        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
