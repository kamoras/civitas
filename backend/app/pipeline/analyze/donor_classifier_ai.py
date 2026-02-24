"""
Hybrid donor classifier — tiered strategy for donor type and industry.

Classification tiers (donor TYPE):
1. FEC committee type codes (structured data from the API itself)
2. Semantic candidate-affiliated detection (embedding similarity)
3. Minimal safety-net rules (payment processor skips only)
4. Learning store lookup
5. Nearest-neighbor embedding classifier for remaining unknowns

Classification tiers (donor INDUSTRY):
1. Learning store
2. Embedding cosine similarity (from industry_classifier)
3. Nearest-neighbor embedding classifier for remaining unknowns

Design rationale: FEC already encodes committee type in structured fields
(committee_type, designation). Using those is faster and more accurate
than asking an LLM to re-derive what the FEC already knows. The kNN
classifier handles the remaining ~5-10% in seconds rather than hours.

Semantic detection replaces ~200 lines of hardcoded string patterns
with embedding cosine similarity against category prototypes. This
generalizes to unseen entities (e.g., new senator names, new PACs)
without requiring code changes.
"""

import logging

import numpy as np
from sqlalchemy.orm import Session

from app.models import LearnedClassification
from app.pipeline.analyze.nn_classifier import (
    classify_batch_nn,
    normalize_learning_store,
    DONOR_TYPE_PROTOTYPES,
)
from app.pipeline.transform.industry_classifier import (
    classify_industries_batch,
    INDUSTRY_DESCRIPTIONS,
    store_llm_classifications,
)

logger = logging.getLogger(__name__)

# FEC contributor entity_type codes → donor types
FEC_ENTITY_TYPE_MAP = {
    "CCM": "CandidateAffiliated",
    "CAN": "CandidateAffiliated",
    "PAC": "PAC",
    "COM": "PAC",
    "ORG": "Org/Employees",
    "IND": "Org/Employees",
    "PTY": "Party/Ideological",
}

AFFILIATED_RECEIPT_TYPES = {"18G", "18H", "18K", "18J", "22G", "22H"}

# Minimal safety-net: payment processors that must always be skipped.
# These are financial conduits (not actual donors) and misclassifying
# them would pollute every senator's donor list.
SKIP_NAMES = {"WINRED", "ACTBLUE", "ANEDOT"}

# Embedding prototypes for semantic donor-type classification.
# Each description captures the semantic signature of a donor category
# so the embedding model can generalize to unseen entities.
_CANDIDATE_AFFILIATED_PROTOTYPE = (
    "candidate personal campaign committee senator victory fund "
    "friends of for senate for congress reelect leadership pac "
    "joint fundraising state victory"
)
_PARTY_PROTOTYPE = (
    "national party committee democratic republican senatorial "
    "congressional campaign committee DSCC NRSC DCCC NRCC "
    "Emily's List Club for Growth Senate Majority PAC "
    "ideological super PAC political action crossroads"
)
_ORG_EMPLOYEES_PROTOTYPE = (
    "corporation company business employees organization firm "
    "group LLC LLP inc bank hospital university hotel "
    "airline insurance manufacturing construction"
)
_PAC_PROTOTYPE = (
    "political action committee PAC fund committee league caucus "
    "association political contributions campaign donations"
)

_SEMANTIC_PROTOTYPES = {
    "CandidateAffiliated": _CANDIDATE_AFFILIATED_PROTOTYPE,
    "Party/Ideological": _PARTY_PROTOTYPE,
    "Org/Employees": _ORG_EMPLOYEES_PROTOTYPE,
    "PAC": _PAC_PROTOTYPE,
}

_semantic_embeddings: dict[str, np.ndarray] | None = None


def _get_semantic_type_embeddings() -> dict[str, np.ndarray]:
    """Pre-compute embeddings for donor type prototype descriptions."""
    global _semantic_embeddings
    if _semantic_embeddings is not None:
        return _semantic_embeddings

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    _semantic_embeddings = {}
    for dtype, desc in _SEMANTIC_PROTOTYPES.items():
        emb = model.encode([desc], show_progress_bar=False)[0]
        _semantic_embeddings[dtype] = emb / np.linalg.norm(emb)

    return _semantic_embeddings


def classify_donor_type_semantic(
    name: str,
    candidate_name: str | None = None,
    threshold: float = 0.35,
) -> str | None:
    """Classify donor type using embedding similarity against prototypes.

    For candidate-affiliated detection, also generates a dynamic template
    using the candidate's last name so the model can detect entities like
    "Sullivan Victory" or "Cruz for Senate" without hardcoded patterns.
    """
    if not name or len(name.strip()) < 3:
        return None

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()
    type_embs = _get_semantic_type_embeddings()

    query_emb = model.encode([name], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_type: str | None = None
    best_score = threshold

    for dtype, emb in type_embs.items():
        score = float(np.dot(query_emb, emb))
        if score > best_score:
            best_score = score
            best_type = dtype

    # Dynamic candidate-affiliated check: if the senator's last name
    # appears in the donor name, generate a personalized template and
    # check similarity. This generalizes to any senator.
    if candidate_name:
        last_name = candidate_name.split(",")[0].strip().upper()
        if len(last_name) > 2 and last_name in name.upper():
            template = (
                f"{last_name} senate campaign victory fund committee "
                f"friends of {last_name} for senate reelect {last_name}"
            )
            template_emb = model.encode([template], show_progress_bar=False)[0]
            template_emb = template_emb / np.linalg.norm(template_emb)
            affil_score = float(np.dot(query_emb, template_emb))
            if affil_score > 0.30:
                return "CandidateAffiliated"

            # Also detect personal contributions: "LAST, FIRST MIDDLE"
            name_upper = name.upper().strip()
            donor_parts = [p.strip() for p in name_upper.split(",")]
            cand_parts = [p.strip() for p in candidate_name.upper().split(",")]
            if len(donor_parts) >= 2 and len(cand_parts) >= 2:
                if donor_parts[0] == cand_parts[0]:
                    cand_tokens = set(cand_parts[1].split())
                    donor_tokens = set(
                        donor_parts[1].replace("'", "").replace('"', "").split()
                    )
                    if cand_tokens & donor_tokens:
                        return "CandidateAffiliated"

    return best_type


def classify_donor_type_from_fec(receipt: dict) -> str | None:
    """Classify donor type using FEC structured metadata on the *contributor*.

    Uses entity_type (contributor's entity kind) and receipt_type (transfer
    category) — NOT committee.committee_type, which describes the *receiving*
    campaign committee and would misclassify every donor.
    """
    receipt_type = receipt.get("receipt_type") or ""
    if receipt_type in AFFILIATED_RECEIPT_TYPES:
        return "CandidateAffiliated"

    entity_type = receipt.get("entity_type") or ""
    if entity_type and entity_type in FEC_ENTITY_TYPE_MAP:
        return FEC_ENTITY_TYPE_MAP[entity_type]

    return None


def is_skip_entity(name_upper: str) -> bool:
    """Check if a donor is a payment processor that should be skipped."""
    return any(skip in name_upper for skip in SKIP_NAMES)


async def classify_donors_hybrid(
    donors: list[dict],
    db_session: Session | None = None,
    on_progress=None,
    candidate_name: str | None = None,
) -> dict[str, dict]:
    """Classify donors using the full tiered strategy.

    Args:
        donors: List of dicts with 'name' and optionally 'amount', 'fec_receipt'.
        db_session: SQLAlchemy session for learning store access.
        candidate_name: FEC candidate name ("LAST, FIRST M") for
            semantic candidate-affiliated detection.

    Returns:
        Dict mapping UPPERCASE donor name -> {type, industry, skip}
    """
    if not donors:
        return {}

    _reset_pending_learnings()
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
    needs_nn: list[dict] = []

    known_from_db: dict[str, dict] = {}
    if db_session is not None:
        unique_names = [d["name"].upper().strip() for d in unique_donors]
        _DB_BATCH = 500
        for batch_start in range(0, len(unique_names), _DB_BATCH):
            batch = unique_names[batch_start : batch_start + _DB_BATCH]
            rows = (
                db_session.query(LearnedClassification)
                .filter(
                    LearnedClassification.entity_name.in_(batch),
                    LearnedClassification.entity_type.in_(["donor_type", "industry"]),
                )
                .all()
            )
            for r in rows:
                if r.entity_name not in known_from_db:
                    known_from_db[r.entity_name] = {}
                known_from_db[r.entity_name][r.entity_type] = r.value

    tier_stats = {"fec": 0, "semantic": 0, "learned": 0, "embedding": 0, "nn_needed": 0}

    names_needing_embedding: list[str] = []
    for donor in unique_donors:
        name_upper = (donor.get("name") or "").upper().strip()
        learned = known_from_db.get(name_upper, {})
        if "donor_type" not in learned or "industry" not in learned:
            names_needing_embedding.append(donor["name"])

    embedding_results: dict[str, str] = {}
    if names_needing_embedding:
        logger.info("Batch embedding %d donor names for industry classification...", len(names_needing_embedding))
        embedding_results = classify_industries_batch(names_needing_embedding)

    for donor in unique_donors:
        name = donor["name"]
        name_upper = name.upper().strip()
        fec_receipt = donor.get("fec_receipt", {})

        donor_type = None
        industry = None
        source_type = None

        # Skip payment processors immediately
        if is_skip_entity(name_upper):
            results[name_upper] = {"type": "SKIP", "industry": "OTHER", "skip": True}
            continue

        learned = known_from_db.get(name_upper, {})
        if "donor_type" in learned and "industry" in learned:
            donor_type = learned["donor_type"]
            industry = learned["industry"]
            source_type = "learned"
            tier_stats["learned"] += 1
        else:
            # Tier 1: FEC structured metadata (highest confidence)
            fec_type = classify_donor_type_from_fec(fec_receipt) if fec_receipt else None
            if fec_type:
                donor_type = fec_type
                source_type = "fec"
                tier_stats["fec"] += 1
            else:
                # Tier 2: Semantic classification (embedding similarity)
                # Use per-donor candidate_name if available, otherwise the
                # function-level candidate_name
                donor_cand = donor.get("candidate_name") or candidate_name
                sem_type = classify_donor_type_semantic(
                    name, candidate_name=donor_cand
                )
                if sem_type:
                    donor_type = sem_type
                    source_type = "semantic"
                    tier_stats["semantic"] += 1

            if industry is None:
                industry = embedding_results.get(name, "OTHER")
                if industry != "OTHER":
                    tier_stats["embedding"] += 1

        if donor_type and industry and industry != "OTHER":
            results[name_upper] = {
                "type": donor_type,
                "industry": industry,
                "skip": False,
            }
            if db_session is not None and source_type != "learned":
                _store_donor_learning(db_session, name_upper, donor_type, industry, source_type or "embedding")
        elif donor_type:
            results[name_upper] = {
                "type": donor_type,
                "industry": industry or "OTHER",
                "skip": False,
            }
            if industry == "OTHER":
                needs_nn.append(donor)
            if db_session is not None and source_type and source_type != "learned":
                _store_donor_learning(db_session, name_upper, donor_type, industry or "OTHER", source_type)
        else:
            needs_nn.append(donor)
            tier_stats["nn_needed"] += 1

    logger.info(
        "Donor classification tiers: %d FEC, %d semantic, %d learned, %d embedding, %d need kNN (of %d total)",
        tier_stats["fec"], tier_stats["semantic"], tier_stats["learned"],
        tier_stats["embedding"], tier_stats["nn_needed"], len(unique_donors),
    )

    if needs_nn and db_session is not None:
        nn_results = _classify_remaining_via_nn(needs_nn, db_session, on_progress=on_progress)
        for name_upper, classification in nn_results.items():
            existing = results.get(name_upper, {})
            merged = {**existing, **classification}
            merged["skip"] = merged.get("type") == "SKIP"
            results[name_upper] = merged

    if db_session is not None:
        db_session.commit()

    return results


def _classify_remaining_via_nn(
    donors: list[dict],
    db_session: Session,
    on_progress=None,
) -> dict[str, dict]:
    """Nearest-neighbor fallback for donors not classified by faster tiers.

    Uses sentence-transformer embeddings to find the most similar
    already-classified donors in the learning store.
    """
    if not donors:
        return {}

    dedup: dict[str, dict] = {}
    for d in donors:
        key = (d.get("name") or "").upper().strip()
        if not key or key == "UNKNOWN":
            continue
        existing = dedup.get(key)
        if existing is None or (d.get("amount") or 0) > (existing.get("amount") or 0):
            dedup[key] = d
    unique_donors = list(dedup.values())
    query_names = [d["name"] for d in unique_donors]

    logger.info("kNN classifying %d unique donors (deduped from %d)...", len(query_names), len(donors))

    normalize_learning_store(db_session)

    industry_results = classify_batch_nn(
        query_names, db_session, entity_type="industry",
        prototype_descriptions=INDUSTRY_DESCRIPTIONS, k=7, min_similarity=0.20,
    )

    type_results = classify_batch_nn(
        query_names, db_session, entity_type="donor_type",
        prototype_descriptions=DONOR_TYPE_PROTOTYPES, k=5, min_similarity=0.25,
    )

    all_results: dict[str, dict] = {}
    industry_learnings: dict[str, str] = {}

    for donor in unique_donors:
        name = donor["name"]
        name_upper = name.upper().strip()
        industry = industry_results.get(name, "OTHER")
        dtype = type_results.get(name, "Org/Employees")

        all_results[name_upper] = {"type": dtype, "industry": industry}

        if industry != "OTHER":
            industry_learnings[name_upper] = industry

        _store_donor_learning(db_session, name_upper, dtype, industry, "nn")

    logger.info("kNN classification complete: %d donors classified", len(all_results))

    if industry_learnings:
        store_llm_classifications(industry_learnings, db_session)

    if on_progress is not None:
        try:
            on_progress()
        except Exception:
            pass

    return all_results


_CONFIDENCE_MAP = {"fec": 1.0, "rules": 0.95, "semantic": 0.9, "embedding": 0.9, "nn": 0.75, "llm": 0.7}

# Tracks writes this run to skip redundant SQL queries for the same entity
_seen_this_run: dict[tuple[str, str], float] = {}


def _store_donor_learning(
    db_session: Session,
    name_upper: str,
    donor_type: str,
    industry: str,
    source: str,
    match_metadata: dict | None = None,
) -> None:
    """Store both type and industry classifications using SQL upsert.

    Uses INSERT ... ON CONFLICT DO UPDATE to handle races atomically.
    Only overwrites if new confidence >= existing confidence.
    """
    import json
    from datetime import datetime
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.pipeline.vector_store import get_model_version

    confidence = _CONFIDENCE_MAP.get(source, 0.5)
    model_ver = get_model_version() if source in ("embedding", "nn", "semantic") else None
    meta_json = json.dumps(match_metadata) if match_metadata else None

    for entity_type, value in [("donor_type", donor_type), ("industry", industry)]:
        key = (name_upper, entity_type)

        prev_confidence = _seen_this_run.get(key, -1.0)
        if prev_confidence > confidence:
            continue

        stmt = sqlite_insert(LearnedClassification).values(
            entity_name=name_upper,
            entity_type=entity_type,
            value=value,
            confidence=confidence,
            source=source,
            model_version=model_ver,
            match_metadata=meta_json,
            learned_at=datetime.utcnow(),
        ).on_conflict_do_update(
            index_elements=["entity_name", "entity_type"],
            set_={
                "value": value,
                "confidence": confidence,
                "source": source,
                "model_version": model_ver,
                "match_metadata": meta_json,
                "learned_at": datetime.utcnow(),
            },
            where=(LearnedClassification.confidence <= confidence),
        )
        db_session.execute(stmt)
        _seen_this_run[key] = confidence


def _reset_pending_learnings() -> None:
    """Clear per-run dedup tracker (call at start of each pipeline run)."""
    _seen_this_run.clear()
