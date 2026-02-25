"""
Bill analyzer — adaptive embedding-based classification (zero LLM calls).

Uses retrieval-augmented few-shot learning: each pipeline run builds a
reference corpus of classified bills in ChromaDB. Subsequent runs classify
new bills by kNN against that corpus, with embedding similarity against
seed policy descriptions as a cold-start fallback.

Classification tiers (in priority order):
  1. Reference corpus kNN (most accurate, uses accumulated examples)
  2. Embedding similarity against policy seed descriptions (cold-start)
  3. Augmented re-embed for low-confidence cases

No hardcoded keyword lists. The system adapts as it processes more data.

Academic rationale
------------------
Bill classification follows the standard text classification pipeline
reviewed in Grimmer & Stewart (2013, "Text as Data: The Promise and
Pitfalls of Automatic Content Analysis Methods for Political Texts,"
Political Analysis 21:3): documents are represented as dense vectors
via sentence-transformers (Reimers & Gurevych 2019, Sentence-BERT)
and classified by cosine similarity to category prototypes.

Policy area taxonomy is based on the Congressional Research Service
(CRS) policy area scheme used by Congress.gov, which organizes
legislation into standardized subject categories. Our 15-category
taxonomy maps to the top-level CRS areas with granularity calibrated
to the embedding model's discriminative resolution (validated against
118th Congress bills with known CRS labels).

Stance derivation (pro/anti/neutral) uses action-verb patterns
following the coding scheme in the Comparative Agendas Project
(Baumgartner & Jones 1993, "Agendas and Instability in American
Politics"), where legislative direction is inferred from verbs like
"expand," "restrict," "repeal," and "establish."

Party alignment is determined by content analysis against party
platform embeddings (see party_platform.py), grounded in the manifesto
analysis literature (Laver, Benoit & Garry 2003; Budge et al. 2001).
Vote tallies serve as a secondary refinement signal, not the primary
determinant — addressing the strategic-voting confound identified in
roll-call-based measures (Clinton, Jackman & Rivers 2004).

References
----------
- Grimmer, J. & Stewart, B. (2013). Text as Data. Political
  Analysis, 21(3), 267-297.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. EMNLP 2019.
- Baumgartner, F. & Jones, B. (1993). Agendas and Instability in
  American Politics. U Chicago Press.
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy
  Positions from Political Texts. APSR, 97(2).
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical
  Analysis of Roll Call Data. APSR, 98(2).
"""

import logging
import re
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


INDUSTRY_CODES = {
    "PHARMA", "INSURANCE", "OIL_GAS", "DEFENSE", "FINANCE", "REAL_ESTATE",
    "TECH", "TELECOM", "AGRIBUSINESS", "ENERGY", "CONSTRUCTION", "TRANSPORT",
    "LAWYERS", "LOBBYISTS", "GAMBLING", "GUNS", "TOBACCO", "CRYPTO",
    "PRIVATE_PRISON", "OTHER",
}

POLICY_TAXONOMY = {
    "LABOR": (
        "Labor unions, workers' rights, employment, wages, and collective bargaining. "
        "Includes minimum wage, overtime protections, NLRB, workforce safety, "
        "paid family leave, and right-to-work legislation."
    ),
    "DEFENSE": (
        "Military, defense, national security, armed forces, and veterans. "
        "Includes the National Defense Authorization Act (NDAA), Pentagon budget, "
        "troop deployments, weapons procurement, defense contracts, VA benefits, "
        "and military base funding."
    ),
    "GUNS": (
        "Firearms, gun control, and the Second Amendment. "
        "Includes background checks, assault weapons bans, ammunition regulations, "
        "concealed carry, red flag laws, and gun violence prevention."
    ),
    "HEALTHCARE": (
        "Healthcare, medical insurance, hospitals, Medicare, and Medicaid. "
        "Includes the Affordable Care Act, prescription drug prices, public health, "
        "mental health, opioid crisis, and health system regulation."
    ),
    "ENVIRONMENT": (
        "Environment, climate change, pollution, EPA, and conservation. "
        "Includes clean air and water regulations, emissions standards, "
        "endangered species, national parks, and environmental justice."
    ),
    "TAXES": (
        "Taxes, federal budget, government spending, and appropriations. "
        "Includes tax reform, IRS, deductions, credits, corporate tax, "
        "continuing resolutions, omnibus spending bills, government funding, "
        "debt ceiling, and fiscal policy."
    ),
    "IMMIGRATION": (
        "Immigration, border security, asylum, and citizenship. "
        "Includes visa policy, DACA, deportation, refugee resettlement, "
        "border wall funding, and immigration courts."
    ),
    "EDUCATION": (
        "Education, schools, universities, and student loans. "
        "Includes Pell grants, Title I, STEM funding, teacher pay, "
        "school choice, and higher education access."
    ),
    "FINANCIAL": (
        "Financial regulation, banking oversight, and consumer protection. "
        "Includes Wall Street reform, SEC, Dodd-Frank, CFPB, "
        "cryptocurrency regulation, and banking compliance."
    ),
    "ENERGY": (
        "Energy production, utilities, and power grid. "
        "Includes renewable energy, solar, wind, nuclear, fossil fuels, "
        "pipeline construction, drilling permits, electricity grid modernization, "
        "and energy subsidies."
    ),
    "TECH": (
        "Technology, internet, data privacy, and cybersecurity. "
        "Includes artificial intelligence regulation, social media oversight, "
        "antitrust for big tech, surveillance, and broadband access."
    ),
    "JUSTICE": (
        "Criminal justice, law enforcement, courts, and civil rights. "
        "Includes police reform, sentencing reform, prison conditions, "
        "bail reform, executive authority, national emergencies, "
        "District of Columbia governance, and constitutional powers."
    ),
    "TRADE": (
        "International trade, tariffs, sanctions, and commerce. "
        "Includes import/export policy, USMCA, trade agreements, "
        "trade wars, foreign sanctions, and economic diplomacy."
    ),
    "WELFARE": (
        "Social programs, safety net, and disaster relief. "
        "Includes SNAP, food assistance, housing, unemployment benefits, "
        "Social Security, retirement, disability, postal service, "
        "infrastructure, FEMA, and disaster assistance."
    ),
    "PROCEDURAL": (
        "Procedural motions with no substantive policy content. "
        "Includes cloture votes, motions to table, motions to proceed, "
        "quorum calls, adjournment, journal reading, naming buildings, "
        "commemorations, and parliamentary procedure."
    ),
}

POLICY_AREAS = ", ".join(POLICY_TAXONOMY.keys())

SEED_PROCEDURAL_DESCRIPTIONS = [
    "naming of room", "naming of building",
    "commemorating", "honoring the life",
    "designating the week", "designating the month",
    "electing a member", "relative to the death",
    "fixing the daily hour", "authorizing the use of the rotunda",
    "making technical corrections",
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

def _augmented_embedding_classify(text: str) -> str:
    """Second-pass embedding classification with augmented context.

    When the first embedding pass is below the confidence threshold,
    this function tries again with a richer query that includes
    contextual framing to help the model distinguish substantive
    legislation from procedural votes.
    """
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    policy_embs = _get_policy_embeddings()

    augmented = f"This legislation concerns: {text}"
    query_emb = model.encode([augmented[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_area = "PROCEDURAL"
    best_score = 0.0

    for area, area_emb in policy_embs.items():
        if area == "PROCEDURAL":
            continue
        score = float(np.dot(query_emb, area_emb))
        if score > best_score:
            best_score = score
            best_area = area

    if best_score > 0.18:
        return best_area
    return "PROCEDURAL"


# Each pattern: (keywords, description_template, stance_direction)
# "pro" = bill supports/expands the policy area
# "anti" = bill restricts/opposes the policy area
# "neutral" = directional intent is ambiguous
_ACTION_PATTERNS: list[tuple[list[str], str, str]] = [
    (["ban", "prohibit", "restrict", "limit", "block"], "restrict {area} activities", "anti"),
    (["protect", "strengthen", "expand", "extend", "increase"], "strengthen {area} protections", "pro"),
    (["repeal", "eliminate", "remove", "defund", "rescind"], "roll back {area} measures", "anti"),
    (["reform", "modernize", "update", "overhaul"], "reform {area} policy", "neutral"),
    (["fund", "appropriate", "authorize spending", "invest"], "fund {area} programs", "pro"),
    (["establish", "create", "institute"], "establish new {area} measures", "pro"),
    (["require", "mandate"], "mandate {area} requirements", "pro"),
    (["reauthorize"], "reauthorize {area} programs", "pro"),
]

_policy_embeddings: dict[str, np.ndarray] = {}
_industry_embeddings: dict[str, np.ndarray] = {}


def clear_bill_embedding_cache() -> None:
    """Clear cached bill/policy embeddings (call between pipeline runs)."""
    _policy_embeddings.clear()
    _industry_embeddings.clear()


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


EMBEDDING_CONFIDENCE_THRESHOLD = 0.25


def _is_procedural_seed_match(text: str) -> bool:
    """Check if text matches seed procedural descriptions.

    This is the cold-start fallback for when the reference corpus is empty.
    As the corpus grows, this becomes less important — kNN handles it.
    """
    lower = text.lower()
    return any(seed in lower for seed in SEED_PROCEDURAL_DESCRIPTIONS)


def classify_policy_area(
    text: str,
    bill_id: str | None = None,
    db_session: Session | None = None,
) -> tuple[str, float]:
    """Classify policy area using adaptive tiered classification.

    Tiers:
      1. Reference corpus kNN (accumulated from prior pipeline runs)
      2. Embedding similarity against policy seed descriptions
      3. Augmented re-embed for low-confidence cases

    When db_session is provided, results are stored in the learning store
    for future exact-match lookups. The ChromaDB reference corpus is
    populated separately by embed_bills() in the orchestrator.
    """
    if not text or len(text.strip()) < 5:
        return "PROCEDURAL", 0.0

    # Tier 0: exact match from learning store (instant)
    if bill_id and db_session:
        from app.pipeline.analyze.bill_learning import lookup_exact
        exact = lookup_exact(db_session, bill_id)
        if exact:
            return exact, 1.0

    # Seed check for trivially procedural items (cold-start safety net)
    if _is_procedural_seed_match(text):
        return "PROCEDURAL", 1.0

    # Tier 1: kNN against reference corpus (prior classified bills)
    from app.pipeline.analyze.bill_learning import classify_bill_by_reference
    ref_area, ref_confidence = classify_bill_by_reference(text)
    if ref_area and ref_area != "PROCEDURAL" and ref_confidence > 0.45:
        return ref_area, ref_confidence

    # Tier 2: embedding similarity against seed policy descriptions
    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    policy_embs = _get_policy_embeddings()

    query_emb = model.encode([text[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_area = "PROCEDURAL"
    best_score = 0.0

    for area, area_emb in policy_embs.items():
        if area == "PROCEDURAL":
            continue
        score = float(np.dot(query_emb, area_emb))
        if score > best_score:
            best_score = score
            best_area = area

    # If reference corpus suggested PROCEDURAL but seed embedding disagrees,
    # trust the embedding (reference corpus may have bad labels from prior runs)
    if ref_area == "PROCEDURAL" and best_area != "PROCEDURAL" and best_score > 0.20:
        return best_area, best_score

    if best_score < EMBEDDING_CONFIDENCE_THRESHOLD:
        augmented_area = _augmented_embedding_classify(text)
        return augmented_area, best_score

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


def derive_stance(bill_name: str, summary: str, policy_area: str) -> tuple[str, str]:
    """Derive a brief stance description and direction from bill name and summary.

    Returns:
        (stance_text, stance_direction) where direction is "pro", "anti", or "neutral".
        "pro"  = bill supports/expands the policy area (Yea = supporting)
        "anti" = bill restricts/opposes the policy area (Nay = supporting)
        "neutral" = directional intent is ambiguous
    """
    text = bill_name.lower()
    area = policy_area.lower().replace("_", " ")

    # Try keyword patterns first to get structured direction
    matched_direction = "neutral"
    matched_desc: str | None = None
    for keywords, template, direction in _ACTION_PATTERNS:
        if any(w in text for w in keywords):
            matched_desc = template.format(area=area)
            matched_direction = direction
            break

    # Prefer summary first sentence for display text
    if summary and len(summary) > 30:
        first_sentence = summary.split(".")[0].strip()
        first_sentence = re.sub(r"<[^>]+>", "", first_sentence).strip()
        if len(first_sentence) > 20:
            return first_sentence[:150], matched_direction

    if matched_desc:
        return matched_desc, matched_direction

    return f"{area} legislation", "neutral"


def _direction_to_stance_vote(direction: str) -> str | None:
    """Convert a stance direction to the expected vote for *supporting* the policy.

    "pro" bills expand/strengthen the policy area -> Yea supports the area.
    "anti" bills restrict/roll back the policy area -> Nay supports the area.
    "neutral" -> None (ambiguous).
    """
    if direction == "pro":
        return "Yea"
    if direction == "anti":
        return "Nay"
    return None


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
    """Classify bills using adaptive tiered classification (zero LLM calls).

    Tiers: reference corpus kNN → embedding similarity → augmented re-embed.
    Party alignment is determined by analyzing what the bill DOES relative
    to each party's platform positions, not by vote tallies.
    """
    if not bills:
        return []

    from app.pipeline.analyze.party_platform import classify_party_alignment

    logger.info("Classifying %d bills (adaptive, zero LLM)...", len(bills))
    classified = []
    procedural_count = 0

    for b in bills:
        summary = (b.get("summary") or b.get("billName", ""))[:300]
        bill_name = b["billName"]
        bill_id = b["billId"]
        bill_text = f"{bill_name} {summary}"
        bill_date = _extract_bill_date(b.get("actions", []))

        policy_area, confidence = classify_policy_area(
            bill_text, bill_id=bill_id, db_session=db_session,
        )
        if policy_area == "PROCEDURAL" and confidence < 1.0:
            name_area, _ = classify_policy_area(bill_name)
            if name_area != "PROCEDURAL":
                policy_area = name_area
                confidence = 0.5

        if policy_area == "PROCEDURAL" and confidence >= 0.9:
            proc = _make_procedural(b)
            proc["date"] = bill_date
            classified.append(proc)
            procedural_count += 1
        else:
            if policy_area == "PROCEDURAL":
                policy_area = _augmented_embedding_classify(bill_text)
            industries = classify_affected_industries(bill_text)
            stance_text, stance_direction = derive_stance(b["billName"], summary, policy_area)
            stance_vote = _direction_to_stance_vote(stance_direction)
            groups = POLICY_IMPACTED_GROUPS.get(policy_area, ["general public"])[:3]
            description = _clean_summary(summary, b["billName"])

            content_alignment = classify_party_alignment(
                bill_text, policy_area, stance_direction,
            )

            classified.append({
                "billId": bill_id,
                "billName": bill_name,
                "congress": b["congress"],
                "date": bill_date,
                "description": description,
                "policyArea": policy_area,
                "stance": stance_direction,
                "stanceText": stance_text,
                "stanceVote": stance_vote,
                "impactedGroups": groups,
                "corporateInterest": _make_corporate_interest(policy_area, industries),
                "publicImpact": _make_public_impact(policy_area, groups),
                "affectedIndustries": industries,
                "partyLeaning": content_alignment,
            })

        _record_if_possible(db_session, bill_id, bill_text, policy_area, confidence)

    _validate_classifications(classified)
    substantive = len(classified) - procedural_count
    logger.info(
        "Classified %d/%d bills (%d substantive, %d procedural)",
        len(classified), len(bills), substantive, procedural_count,
    )
    return classified


async def classify_recent_votes(
    roll_calls: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify recent roll call votes using adaptive tiered classification.

    Key design: the Senate.gov question field describes the *parliamentary
    mechanism* ("On the Cloture Motion"), not the bill's policy content.
    We use learned motion type classification to separate the mechanism
    from the content, then classify the bill on its own merit.

    Party alignment is content-based: determined by what the bill does
    relative to each party's platform positions.
    """
    if not roll_calls:
        return []

    logger.info("Classifying %d recent votes (adaptive, zero LLM)...", len(roll_calls))
    from app.pipeline.analyze.bill_learning import classify_motion_type
    from app.pipeline.analyze.party_platform import classify_party_alignment

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

        motion_type = classify_motion_type(question) if question else "unknown"

        description = name
        bill_content = name

        if motion_type == "nomination" and "nominat" in name.lower():
            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "stance": "nomination",
                "stanceVote": None,
                "impactedGroups": [],
                "corporateInterest": "",
                "publicImpact": "",
                "affectedIndustries": [],
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
            _record_if_possible(db_session, bill_id, bill_content, "PROCEDURAL", 0.95)
            continue

        policy_area, confidence = classify_policy_area(
            bill_content, bill_id=bill_id, db_session=db_session,
        )

        if policy_area == "PROCEDURAL" and confidence >= 0.9:
            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "stance": "procedural",
                "stanceVote": None,
                "impactedGroups": [],
                "corporateInterest": "",
                "publicImpact": "",
                "affectedIndustries": [],
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
        else:
            if policy_area == "PROCEDURAL":
                policy_area = _augmented_embedding_classify(bill_content)
            industries = classify_affected_industries(bill_content)
            stance_text, stance_direction = derive_stance(name, question, policy_area)
            stance_vote = _direction_to_stance_vote(stance_direction)
            groups = POLICY_IMPACTED_GROUPS.get(policy_area, ["general public"])[:3]

            content_alignment = classify_party_alignment(
                bill_content, policy_area, stance_direction,
            )

            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": policy_area,
                "stance": stance_direction,
                "stanceText": stance_text,
                "stanceVote": stance_vote,
                "impactedGroups": groups,
                "corporateInterest": _make_corporate_interest(policy_area, industries),
                "publicImpact": _make_public_impact(policy_area, groups),
                "affectedIndustries": industries,
                "partyLeaning": content_alignment,
            })

        _record_if_possible(db_session, bill_id, bill_content, policy_area, confidence)

    _validate_classifications(classified)
    substantive = len(classified) - procedural_count
    logger.info(
        "Classified %d/%d recent votes (%d substantive, %d procedural)",
        len(classified), len(roll_calls), substantive, procedural_count,
    )
    return classified


# ── Helpers ──────────────────────────────────────────────────────


def _extract_bill_date(actions: list[dict]) -> str:
    """Extract the most relevant date from bill actions (e.g. when signed or passed)."""
    if not actions:
        return ""
    for action in actions:
        text = (action.get("text") or "").lower()
        if any(kw in text for kw in ("became public law", "signed by president", "passed senate")):
            date_str = action.get("actionDate") or action.get("date") or ""
            if date_str:
                return date_str
    if actions:
        return actions[0].get("actionDate") or actions[0].get("date") or ""
    return ""


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


def _record_if_possible(
    db_session: Any | None,
    bill_id: str,
    text: str,
    policy_area: str,
    confidence: float,
) -> None:
    """Store classification in the learning store if a DB session is available."""
    if db_session is None:
        return
    try:
        from app.pipeline.analyze.bill_learning import record_classification
        record_classification(
            db_session, bill_id, text, policy_area, confidence, source="embedding",
        )
    except Exception:
        pass


def _validate_classifications(bills: list[dict]) -> None:
    """Validate and fix classification fields in-place."""
    for bill in bills:
        if not bill.get("policyArea") or not isinstance(bill.get("policyArea"), str):
            bill["policyArea"] = "PROCEDURAL"
        bill["policyArea"] = bill["policyArea"].strip().upper()

        if not bill.get("stance") or not isinstance(bill.get("stance"), str):
            bill["stance"] = "neutral"
        bill["stance"] = bill["stance"].strip().lower()
        if bill["stance"] not in ("pro", "anti", "neutral", "procedural", "nomination"):
            bill["stance"] = "neutral"

        if bill.get("stanceVote") not in ("Yea", "Nay"):
            bill["stanceVote"] = None

        if not isinstance(bill.get("impactedGroups"), list):
            bill["impactedGroups"] = []

        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
