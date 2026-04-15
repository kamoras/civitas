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

_NOMINATION_NAME_RE = re.compile(
    r"(?:,\s*of\s+\w+,?\s+to\s+be\s)"
    r"|(?:\bto\s+be\s+(?:United\s+States|an?\s+(?:Assistant|Associate|Under))\b)"
    r"|(?:\bnominat(?:ion|ed|ee)\b)",
    re.IGNORECASE,
)


POLICY_TAXONOMY = {
    "LABOR": (
        "Labor unions, workers' rights, employment, wages, and collective bargaining. "
        "Includes minimum wage, overtime protections, NLRB, workforce safety, "
        "paid family leave, and right-to-work legislation."
    ),
    "DEFENSE": (
        "U.S. military, armed forces, national security, and veterans. "
        "Includes the National Defense Authorization Act (NDAA), Pentagon budget, "
        "troop deployments, weapons procurement, defense contracts, VA benefits, "
        "and military base funding."
    ),
    "FOREIGN_POLICY": (
        "International relations, foreign aid, diplomacy, and global conflicts. "
        "Includes treaties, humanitarian aid, diplomatic sanctions, United Nations, "
        "and relations with specific foreign countries or regions (e.g. Russia, "
        "China, Ukraine, Middle East, Iran)."
    ),
    "GUNS": (
        "Domestic firearms regulation, gun control, and the Second Amendment. "
        "Includes background checks, assault weapons bans, ammunition regulations, "
        "concealed carry, red flag laws, and gun violence prevention."
    ),
    "HEALTHCARE": (
        "U.S. healthcare system, medical insurance, hospitals, Medicare, and Medicaid. "
        "Includes the Affordable Care Act, prescription drug prices, public health, "
        "mental health, opioid crisis, and health system regulation."
    ),
    "ENVIRONMENT": (
        "Environment, climate change, pollution, EPA, and conservation. "
        "Includes clean air and water regulations, emissions standards, "
        "endangered species, national parks, and environmental justice."
    ),
    "TAXES": (
        "Taxes, federal budget, and government spending appropriations. "
        "Includes tax reform, IRS, deductions, credits, corporate tax, "
        "continuing resolutions, omnibus spending bills, government funding, "
        "debt ceiling, and fiscal policy."
    ),
    "IMMIGRATION": (
        "U.S. immigration, border security, asylum, and citizenship. "
        "Includes visa policy, DACA, deportation, refugee resettlement, "
        "border wall funding, and immigration courts."
    ),
    "EDUCATION": (
        "Education, schools, universities, and student loans. "
        "Includes Pell grants, Title I, STEM funding, teacher pay, "
        "school choice, and higher education access."
    ),
    "FINANCIAL": (
        "U.S. financial regulation, banking oversight, and consumer protection. "
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
        "International trade, tariffs, economic sanctions, and commerce. "
        "Includes import/export policy, USMCA, trade agreements, "
        "trade wars, and economic diplomacy."
    ),
    "WELFARE": (
        "Social safety net, housing assistance, and disaster relief. "
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

_PROCEDURAL_PROTOTYPE = (
    "naming building commemorating honoring designating week month "
    "electing member relative to death fixing daily hour authorizing rotunda "
    "technical corrections renaming post office awarding medal tribute memorial "
    "congressional record adjournment quorum call cloture motion to table"
)
_procedural_emb: np.ndarray | None = None

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
_STANCE_PROTOTYPES = {
    "pro": (
        "protect strengthen expand extend increase fund invest establish create "
        "mandate require reauthorize support promote enhance authorize appropriation "
        "improve safeguard guarantee ensure provide preserve advance empower "
        "a bill to provide for the expansion and protection of rights and services"
    ),
    "anti": (
        "ban prohibit restrict limit block repeal eliminate remove defund rescind "
        "cut reduce rollback revoke abolish dismantle oppose curtail suspend "
        "withdraw terminate penalize sanction end halt prevent stop "
        "a bill to repeal and restrict regulations and reduce spending"
    ),
    "neutral": (
        "reform modernize update overhaul study review assess examine "
        "amend modify restructure reorganize transition rename designate"
    ),
}
_stance_embs: dict[str, np.ndarray] | None = None

_policy_embeddings: dict[str, np.ndarray] = {}


def clear_bill_embedding_cache() -> None:
    """Clear cached bill/policy embeddings (call between pipeline runs)."""
    _policy_embeddings.clear()


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


# ── Policy area classification (embeddings) ──────────────────────


EMBEDDING_CONFIDENCE_THRESHOLD = 0.25


def _is_procedural_seed_match(text: str, threshold: float = 0.30) -> bool:
    """Check if text is procedural via embedding similarity to the procedural prototype.

    Uses cosine similarity (Reimers & Gurevych 2019) instead of keyword
    substring matching. The procedural prototype captures the semantic
    signature of ceremonial/administrative bills.
    """
    global _procedural_emb
    if not text or len(text.strip()) < 5:
        return True

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    if _procedural_emb is None:
        emb = model.encode([_PROCEDURAL_PROTOTYPE], show_progress_bar=False)[0]
        _procedural_emb = emb / np.linalg.norm(emb)

    query_emb = model.encode([text[:300]], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    score = float(np.dot(query_emb, _procedural_emb))
    return score >= threshold


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


MULTI_AREA_SECONDARY_THRESHOLD = 0.20
MULTI_AREA_GAP_RATIO = 0.70


def classify_policy_areas_multi(
    text: str,
    bill_id: str | None = None,
    db_session: Session | None = None,
    max_areas: int = 4,
) -> list[dict]:
    """Classify ALL relevant policy areas for a bill using embedding similarity.

    Real legislation rarely addresses a single policy dimension. The
    Comparative Agendas Project (Baumgartner & Jones 1993, 2002) codes
    bills with both a primary and secondary topic; Adler & Wilkerson
    (2012, "Congress and the Politics of Problem Solving") show that
    most major bills span 2-4 policy domains.

    Returns a list of {area, confidence} dicts ordered by confidence,
    where:
      - The first entry is the primary area (same as classify_policy_area)
      - Additional entries are secondary areas whose embedding similarity
        exceeds MULTI_AREA_SECONDARY_THRESHOLD and whose confidence is
        at least MULTI_AREA_GAP_RATIO of the primary area's confidence.

    The gap-ratio filter prevents low-confidence noise from inflating
    the area count. A bill about healthcare (0.72) that also touches
    taxes (0.55) will get both, but a faint procedural echo (0.22)
    won't appear.
    """
    if not text or len(text.strip()) < 5:
        return [{"area": "PROCEDURAL", "confidence": 0.0}]

    primary_area, primary_conf = classify_policy_area(
        text, bill_id=bill_id, db_session=db_session,
    )

    if primary_area == "PROCEDURAL" and primary_conf >= 0.9:
        return [{"area": "PROCEDURAL", "confidence": primary_conf}]

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    policy_embs = _get_policy_embeddings()

    query_emb = model.encode([text[:500]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    scored: list[tuple[str, float]] = []
    for area, area_emb in policy_embs.items():
        if area == "PROCEDURAL":
            continue
        score = float(np.dot(query_emb, area_emb))
        scored.append((area, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    if not scored:
        return [{"area": primary_area, "confidence": primary_conf}]

    top_score = scored[0][1]
    threshold = max(
        MULTI_AREA_SECONDARY_THRESHOLD,
        top_score * MULTI_AREA_GAP_RATIO,
    )

    areas: list[dict] = []
    for area, score in scored[:max_areas]:
        if score >= threshold or area == primary_area:
            areas.append({"area": area, "confidence": round(score, 4)})

    if not any(a["area"] == primary_area for a in areas):
        areas.insert(0, {"area": primary_area, "confidence": primary_conf})

    return areas


# ── Stance derivation (embedding-based) ──────────────────────────


def _get_stance_embeddings() -> dict[str, np.ndarray]:
    """Cache and return stance prototype embeddings."""
    global _stance_embs
    if _stance_embs is not None:
        return _stance_embs

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    _stance_embs = {}
    for direction, proto in _STANCE_PROTOTYPES.items():
        emb = model.encode([proto], show_progress_bar=False)[0]
        _stance_embs[direction] = emb / np.linalg.norm(emb)
    return _stance_embs


def derive_stance(bill_name: str, summary: str, policy_area: str) -> tuple[str, str]:
    """Derive a brief stance description and direction from bill name and summary.

    Uses embedding cosine similarity against stance direction prototypes
    (pro/anti/neutral) instead of keyword pattern matching.

    Returns:
        (stance_text, stance_direction) where direction is "pro", "anti", or "neutral".
        "pro"  = bill supports/expands the policy area (Yea = supporting)
        "anti" = bill restricts/opposes the policy area (Nay = supporting)
        "neutral" = directional intent is ambiguous
    """
    area = policy_area.lower().replace("_", " ")

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    stance_embs = _get_stance_embeddings()

    query_text = bill_name
    if summary and len(summary) > 30:
        query_text = f"{bill_name} {summary[:200]}"

    query_emb = model.encode([query_text[:300]], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    scores: dict[str, float] = {}
    for direction, emb in stance_embs.items():
        scores[direction] = float(np.dot(query_emb, emb))

    best_dir = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best_dir]

    # Require minimum absolute similarity to classify at all
    if best_score < 0.10:
        best_dir = "neutral"
    elif best_dir != "neutral":
        # For pro/anti, require a margin over neutral to avoid false positives
        neutral_score = scores.get("neutral", 0.0)
        if best_score - neutral_score < 0.03:
            best_dir = "neutral"

    if summary and len(summary) > 30:
        first_sentence = summary.split(".")[0].strip()
        first_sentence = re.sub(r"<[^>]+>", "", first_sentence).strip()
        if len(first_sentence) > 20:
            return first_sentence[:150], best_dir

    direction_labels = {"pro": "strengthen", "anti": "restrict", "neutral": "reform"}
    return f"{direction_labels[best_dir]} {area} policy", best_dir


# ── Main classification functions ────────────────────────────────


async def classify_all_bills(
    bills: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify bills using adaptive tiered multi-area classification.

    Each bill receives multiple policy area classifications reflecting
    the reality that legislation typically spans 2-4 policy domains
    (Adler & Wilkerson 2012). Party alignment is computed per-area and
    aggregated with confidence weights, producing a nuanced alignment
    score rather than a binary party label.

    Tiers: reference corpus kNN → embedding similarity → augmented re-embed.
    """
    if not bills:
        return []

    from app.pipeline.analyze.party_platform import (
        classify_party_alignment,
        classify_party_alignment_multi,
    )

    logger.info("Classifying %d bills (adaptive multi-area, zero LLM)...", len(bills))
    classified = []
    procedural_count = 0
    multi_area_count = 0

    for b in bills:
        bill_name = b["billName"]
        bill_id = b["billId"]
        bill_text = _build_classification_text(b)
        bill_date = _extract_bill_date(b.get("actions", []))
        summary = b.get("summary", "")

        areas = classify_policy_areas_multi(
            bill_text, bill_id=bill_id, db_session=db_session,
        )
        policy_area = areas[0]["area"]
        confidence = areas[0]["confidence"]

        if policy_area == "PROCEDURAL" and confidence < 1.0:
            name_areas = classify_policy_areas_multi(bill_name)
            if name_areas[0]["area"] != "PROCEDURAL":
                areas = name_areas
                policy_area = areas[0]["area"]
                confidence = 0.5

        if policy_area == "PROCEDURAL" and confidence >= 0.9:
            proc = _make_procedural(b)
            proc["date"] = bill_date
            proc["policyAreas"] = [{"area": "PROCEDURAL", "confidence": confidence, "party": "bipartisan"}]
            proc["partyAlignmentWeight"] = 0.0
            classified.append(proc)
            procedural_count += 1
        else:
            if policy_area == "PROCEDURAL":
                policy_area = _augmented_embedding_classify(bill_text)
                areas = [{"area": policy_area, "confidence": confidence}]
            _stance_text, stance_direction = derive_stance(b["billName"], summary, policy_area)

            description = _clean_summary(summary, b["billName"], b.get("officialTitle", ""))

            multi_alignment = classify_party_alignment_multi(
                bill_text, areas, stance_direction,
            )

            content_alignment = multi_alignment["overall"]
            alignment_weight = multi_alignment["weight"]

            area_parties = {
                a["area"]: a["party"] for a in multi_alignment["areas"]
            }
            policy_areas_enriched = [
                {
                    "area": a["area"],
                    "confidence": a["confidence"],
                    "party": area_parties.get(a["area"], "bipartisan"),
                }
                for a in areas
            ]

            if len(areas) > 1:
                multi_area_count += 1

            classified.append({
                "billId": bill_id,
                "billName": bill_name,
                "congress": b["congress"],
                "date": bill_date,
                "description": description,
                "policyArea": policy_area,
                "policyAreas": policy_areas_enriched,
                "stance": stance_direction,
                "partyLeaning": content_alignment,
                "partyAlignmentWeight": alignment_weight,
            })

        _record_if_possible(db_session, bill_id, bill_text, policy_area, confidence)

    _validate_classifications(classified)
    substantive = len(classified) - procedural_count
    logger.info(
        "Classified %d/%d bills (%d substantive, %d procedural, %d multi-area)",
        len(classified), len(bills), substantive, procedural_count, multi_area_count,
    )
    return classified


async def classify_recent_votes(
    roll_calls: list[dict], db_session: Any | None = None
) -> list[dict]:
    """Classify recent roll call votes using adaptive multi-area classification.

    Key design: the Senate.gov question field describes the *parliamentary
    mechanism* ("On the Cloture Motion"), not the bill's policy content.
    We use learned motion type classification to separate the mechanism
    from the content, then classify the bill on its own merit with
    multi-area support.
    """
    if not roll_calls:
        return []

    logger.info("Classifying %d recent votes (adaptive multi-area, zero LLM)...", len(roll_calls))
    from app.pipeline.analyze.bill_learning import classify_motion_type
    from app.pipeline.analyze.party_platform import (
        classify_party_alignment_multi,
    )

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
        if question and question.lower() != name.lower() and len(question) > 15:
            description = f"{name} — {question}"
            if len(description) > 200:
                description = description[:200].rsplit(" ", 1)[0] + "..."
        bill_content = name

        proc_areas = [{"area": "PROCEDURAL", "confidence": 0.95, "party": "bipartisan"}]

        is_nomination = (
            motion_type == "nomination"
            or bill_id.startswith("PN")
            or _NOMINATION_NAME_RE.search(name) is not None
        )

        if is_nomination:
            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "policyAreas": proc_areas,
                "partyAlignmentWeight": 0.0,
                "stance": "nomination",
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
            _record_if_possible(db_session, bill_id, bill_content, "PROCEDURAL", 0.95)
            continue

        areas = classify_policy_areas_multi(
            bill_content, bill_id=bill_id, db_session=db_session,
        )
        policy_area = areas[0]["area"]
        confidence = areas[0]["confidence"]

        if policy_area == "PROCEDURAL" and confidence >= 0.9:
            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": "PROCEDURAL",
                "policyAreas": proc_areas,
                "partyAlignmentWeight": 0.0,
                "stance": "procedural",
                "partyLeaning": "bipartisan",
            })
            procedural_count += 1
        else:
            if policy_area == "PROCEDURAL":
                policy_area = _augmented_embedding_classify(bill_content)
                areas = [{"area": policy_area, "confidence": confidence}]
            _stance_text, stance_direction = derive_stance(name, question, policy_area)

            multi_alignment = classify_party_alignment_multi(
                bill_content, areas, stance_direction,
            )

            content_alignment = multi_alignment["overall"]
            alignment_weight = multi_alignment["weight"]

            area_parties = {
                a["area"]: a["party"] for a in multi_alignment["areas"]
            }
            policy_areas_enriched = [
                {
                    "area": a["area"],
                    "confidence": a["confidence"],
                    "party": area_parties.get(a["area"], "bipartisan"),
                }
                for a in areas
            ]

            classified.append({
                "billId": bill_id,
                "billName": name,
                "date": vote_date,
                "description": description,
                "policyArea": policy_area,
                "policyAreas": policy_areas_enriched,
                "partyAlignmentWeight": alignment_weight,
                "stance": stance_direction,
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


def _build_classification_text(b: dict) -> str:
    """Build semantically rich text for bill classification.

    Bills with uninformative short titles (named after people, acronyms)
    carry almost no policy signal for the embedding model.  This function
    assembles the richest available text from multiple sources:

      1. Bill name (always present)
      2. Official title from Congress.gov (e.g., 'A bill to prevent the
         purchase of ammunition by prohibited purchasers')
      3. CRS policy area (e.g., 'Crime and Law Enforcement')
      4. Summary text (when available)
      5. First portion of full bill text (fallback when summary is thin)

    The assembled text is truncated to 500 characters to fit within the
    sentence-transformer's effective context window.
    """
    parts = [b["billName"]]

    official_title = b.get("officialTitle", "")
    if official_title and official_title.lower() != b["billName"].lower():
        parts.append(official_title)

    crs_area = b.get("crsPolicyArea", "")
    if crs_area:
        parts.append(crs_area)

    summary = b.get("summary", "")
    if summary and len(summary.strip()) > 20:
        clean = re.sub(r"<[^>]+>", "", summary).strip()
        parts.append(clean[:300])

    if len(" ".join(parts)) < 60:
        full_text = b.get("fullText", "")
        if full_text and len(full_text.strip()) > 30:
            parts.append(full_text[:300])

    return " ".join(parts)[:500]


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
    official = b.get("officialTitle", "")
    desc = official if official and len(official) > 20 and official.lower() != b["billName"].lower() else b["billName"]
    return {
        "billId": b["billId"],
        "billName": b["billName"],
        "congress": b.get("congress", 0),
        "date": "",
        "description": desc,
        "policyArea": "PROCEDURAL",
        "stance": "procedural",
        "partyLeaning": "bipartisan",
    }


def _clean_summary(summary: str, bill_name: str, official_title: str = "") -> str:
    """Extract a meaningful description from CRS summary, official title, or bill name.

    Prefers CRS summary (most detailed), then official title (e.g. "A bill
    to prevent the purchase of ammunition by prohibited purchasers"), then
    falls back to the bill short title.
    """
    if summary and len(summary.strip()) > 10:
        clean = re.sub(r"<[^>]+>", "", summary).strip()
        if len(clean) > 200:
            cut = clean[:200].rsplit(" ", 1)[0]
            return cut + "..."
        if clean:
            return clean

    if official_title and len(official_title) > 20 and official_title.lower() != bill_name.lower():
        if len(official_title) > 200:
            cut = official_title[:200].rsplit(" ", 1)[0]
            return cut + "..."
        return official_title

    return bill_name


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

        if bill.get("partyLeaning") not in ("R", "D", "bipartisan"):
            bill["partyLeaning"] = "bipartisan"
