"""
Hybrid donor classifier — tiered strategy for donor type and industry.

Classification tiers (donor TYPE):
1. FEC committee type codes (structured data from the API itself)
2. Deterministic pattern rules (payment processors, party committees)
3. Learning store lookup
4. Nearest-neighbor embedding classifier for remaining unknowns

Classification tiers (donor INDUSTRY):
1. Learning store
2. Embedding cosine similarity (from industry_classifier)
3. Nearest-neighbor embedding classifier for remaining unknowns

Design rationale: FEC already encodes committee type in structured fields
(committee_type, designation). Using those is faster and more accurate
than asking an LLM to re-derive what the FEC already knows. The kNN
classifier handles the remaining ~5-10% in seconds rather than hours.
"""

import logging
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
# The entity_type field on a Schedule A receipt describes the *contributor*,
# whereas committee.committee_type describes the *receiving* committee (the filer).
# We must use entity_type to classify the contributor.
FEC_ENTITY_TYPE_MAP = {
    "CCM": "CandidateAffiliated",  # Candidate Committee
    "CAN": "CandidateAffiliated",  # Candidate
    "PAC": "PAC",
    "COM": "PAC",                  # Committee (generic)
    "ORG": "Org/Employees",
    "IND": "Org/Employees",        # Individual (shouldn't appear in PAC receipts)
    "PTY": "Party/Ideological",    # Party organization
}

# FEC receipt_type codes that indicate affiliated/authorized transfers
AFFILIATED_RECEIPT_TYPES = {"18G", "18H", "18K", "18J", "22G", "22H"}

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



def classify_donor_type_from_fec(receipt: dict) -> str | None:
    """Classify donor type using FEC structured metadata on the *contributor*.

    Uses entity_type (contributor's entity kind) and receipt_type (transfer
    category) — NOT committee.committee_type, which describes the *receiving*
    campaign committee and would misclassify every donor.
    """
    # receipt_type encodes the FEC schedule line — affiliated transfers are
    # always from the candidate's own committees
    receipt_type = receipt.get("receipt_type") or ""
    if receipt_type in AFFILIATED_RECEIPT_TYPES:
        return "CandidateAffiliated"

    # entity_type describes the contributor
    entity_type = receipt.get("entity_type") or ""
    if entity_type and entity_type in FEC_ENTITY_TYPE_MAP:
        return FEC_ENTITY_TYPE_MAP[entity_type]

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
    on_progress=None,
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
    needs_llm: list[dict] = []

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

    tier_stats = {"fec": 0, "rules": 0, "learned": 0, "embedding": 0, "llm_needed": 0}

    # Pre-compute embedding-based industry classifications in one batched call
    # instead of 6000+ individual model.encode() calls that can crash native code.
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
                industry = embedding_results.get(name, "OTHER")
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
        "Donor classification tiers: %d FEC, %d rules, %d learned, %d embedding, %d need kNN (of %d total)",
        tier_stats["fec"], tier_stats["rules"], tier_stats["learned"],
        tier_stats["embedding"], tier_stats["llm_needed"], len(unique_donors),
    )

    if needs_llm and db_session is not None:
        nn_results = _classify_remaining_via_nn(needs_llm, db_session, on_progress=on_progress)
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
        dtype = type_results.get(name, "PAC")

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


_pending_learnings: dict[tuple[str, str], tuple[str, float]] = {}


def _store_donor_learning(
    db_session: Session,
    name_upper: str,
    donor_type: str,
    industry: str,
    source: str,
) -> None:
    """Store both type and industry classifications in the learning store.

    Tracks pending writes in _pending_learnings to avoid duplicate INSERTs
    (autoflush=False means pending adds aren't visible to queries).
    Only overwrites if new confidence >= existing confidence.
    """
    from datetime import datetime

    confidence = {"fec": 1.0, "rules": 0.95, "embedding": 0.9, "nn": 0.75, "llm": 0.7}.get(source, 0.5)

    for entity_type, value in [("donor_type", donor_type), ("industry", industry)]:
        key = (name_upper, entity_type)

        # Check if we already wrote a higher-confidence value this run
        pending = _pending_learnings.get(key)
        if pending and pending[1] > confidence:
            continue

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
        elif key not in _pending_learnings:
            db_session.add(LearnedClassification(
                entity_name=name_upper,
                entity_type=entity_type,
                value=value,
                confidence=confidence,
                source=source,
            ))

        _pending_learnings[key] = (value, confidence)


def _reset_pending_learnings() -> None:
    """Clear pending learning tracker (call at start of each pipeline run)."""
    _pending_learnings.clear()
