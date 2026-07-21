"""
Nearest-neighbor classifier using sentence-transformer embeddings.

Classifies donors by finding the most similar already-labeled entities
in the learning store. Supports dynamic categories — any label that
exists in the learning store is automatically a valid classification target.

Academic rationale
------------------
Instance-based learning via kNN (Cover & Hart 1967, "Nearest Neighbor
Pattern Classification," IEEE Trans. Info Theory 13:1) is chosen over
parametric classifiers because: (1) the category set evolves as new
entity types appear in FEC data, (2) no retraining is needed when
new labeled examples arrive — they are simply added to the reference
set, and (3) the method is naturally non-parametric and adapts to
arbitrary decision boundaries.

Similarity-weighted voting (Dudani 1976) is used instead of unweighted
majority vote, which reduces sensitivity to the choice of k and gives
higher influence to closer neighbors. This is equivalent to a kernel
density estimate with a cosine-similarity kernel in embedding space.

The approach mirrors prototypical networks (Snell, Swersky & Zemel
2017, "Prototypical Networks for Few-Shot Learning," NeurIPS) where
classification is performed by comparing query embeddings to class
prototypes — here, the prototypes are real labeled examples rather
than learned centroids.

This replaces the LLM-based classification fallback, cutting the
classification phase from ~40+ minutes to under 5 seconds while
producing more consistent results (LLM hallucinated invalid categories).

References
----------
- Cover, T. & Hart, P. (1967). IEEE Trans. Info Theory, 13(1), 21-27.
- Dudani, S. (1976). IEEE Trans. SMC, 6(4), 325-327.
- Snell, J. et al. (2017). NeurIPS 2017, 4077-4087.
"""

import logging
from collections import Counter

import numpy as np
from sqlalchemy.orm import Session

from app.config_definitions import INDUSTRIES
from app.models import LearnedClassification

logger = logging.getLogger(__name__)

_category_norm_cache: dict[str, str] = {}

# Canonical donor type names — kNN and learning store may use mixed case
DONOR_TYPE_CANONICAL: dict[str, str] = {
    "PAC": "PAC",
    "ORG/EMPLOYEES": "Org/Employees",
    "CANDIDATEAFFILIATED": "CandidateAffiliated",
    "PARTY/IDEOLOGICAL": "Party/Ideological",
    "SELF-FUNDED": "Self-Funded",
    "SELFFUNDED": "Self-Funded",
    "SELF_FUNDED": "Self-Funded",
    "SKIP": "SKIP",
    "OTHER": "OTHER",
    # Already-correct mixed case forms
    "Org/Employees": "Org/Employees",
    "CandidateAffiliated": "CandidateAffiliated",
    "Party/Ideological": "Party/Ideological",
    "Self-Funded": "Self-Funded",
}

DONOR_TYPE_PROTOTYPES: dict[str, str] = {
    "PAC": "political action committee PAC campaign contributions political fund",
    "Org/Employees": "corporation company employees business organization employer firm group",
    "Party/Ideological": "democratic republican party committee national senatorial congressional club growth",
    "CandidateAffiliated": "candidate senator representative campaign friends of committee elect",
    "Self-Funded": "candidate personal loan self-funded own money personal contribution senator governor",
    "SKIP": (
        "payment processor earmark conduit WinRed ActBlue Anedot joint fundraising "
        "individual person self employed retired homemaker occupation title profession"
    ),
}


def _get_model():
    from app.pipeline.vector_store import get_embedding_model
    return get_embedding_model()


def _normalize_category(value: str) -> str:
    """Map any category string to the nearest valid industry via embedding similarity.

    Uses the industry description embeddings from industry_classifier.py as
    semantic prototypes for each valid category. Unknown/stale categories are
    resolved by cosine similarity (Reimers & Gurevych 2019) rather than a
    hardcoded alias table.
    """
    v = value.strip().upper()
    if v in VALID_INDUSTRIES:
        return v
    if not v or v == "UNKNOWN":
        return "OTHER"
    if v in _category_norm_cache:
        return _category_norm_cache[v]

    from app.pipeline.transform.industry_classifier import _get_industry_embeddings

    model = _get_model()
    industry_embs = _get_industry_embeddings()
    query_emb = model.encode([v.replace("_", " ")], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    best_cat = "OTHER"
    best_score = 0.25
    for cat, cat_emb in industry_embs.items():
        score = float(np.dot(query_emb, cat_emb))
        if score > best_score:
            best_score = score
            best_cat = cat

    _category_norm_cache[v] = best_cat
    if best_cat != "OTHER":
        logger.info("Semantic category normalization: %s -> %s (%.3f)", v, best_cat, best_score)
    return best_cat


def normalize_donor_type(value: str) -> str:
    """Normalize donor type to canonical mixed-case form."""
    return DONOR_TYPE_CANONICAL.get(value, DONOR_TYPE_CANONICAL.get(value.upper(), value))


def _load_references(
    db_session: Session,
    entity_type: str,
    prototype_descriptions: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Load labeled reference examples from the learning store.

    Returns (names_list, labels_list) excluding OTHER/UNKNOWN.
    Prototype descriptions are appended as seed anchors for categories
    that may have few real-world examples.
    """
    skip_values = {"OTHER", "UNKNOWN", "SKIP", ""}

    rows = (
        db_session.query(
            LearnedClassification.entity_name,
            LearnedClassification.value,
        )
        .filter(
            LearnedClassification.entity_type == entity_type,
        )
        .all()
    )

    names: list[str] = []
    labels: list[str] = []

    for name, value in rows:
        if entity_type == "donor_type":
            normalized = normalize_donor_type(value)
        else:
            normalized = _normalize_category(value)
        if normalized in skip_values:
            continue
        names.append(name)
        labels.append(normalized)

    if prototype_descriptions:
        for label, description in prototype_descriptions.items():
            names.append(description)
            labels.append(label)

    return names, labels


def classify_batch_nn(
    query_names: list[str],
    db_session: Session,
    entity_type: str,
    prototype_descriptions: dict[str, str] | None = None,
    k: int = 7,
    min_similarity: float = 0.20,
) -> dict[str, str]:
    """Classify names by kNN in sentence-transformer embedding space.

    For each query name, finds the K most similar labeled entities from
    the learning store and assigns the category by similarity-weighted
    majority vote.

    Args:
        query_names: Names to classify.
        db_session: DB session for loading reference data.
        entity_type: "industry" or "donor_type".
        prototype_descriptions: Optional seed descriptions per category.
        k: Number of nearest neighbors to consider.
        min_similarity: Minimum cosine similarity to count as a neighbor.

    Returns:
        Dict mapping query name -> predicted category.
    """
    if not query_names:
        return {}

    ref_names, ref_labels = _load_references(
        db_session, entity_type, prototype_descriptions,
    )

    if not ref_names:
        logger.warning("No reference examples for entity_type=%s, returning OTHER", entity_type)
        return {name: "OTHER" for name in query_names}

    logger.info(
        "kNN classifier: %d reference examples, %d unique categories, %d queries",
        len(ref_names),
        len(set(ref_labels)),
        len(query_names),
    )

    model = _get_model()

    _BATCH = 256
    ref_embs_parts = []
    for start in range(0, len(ref_names), _BATCH):
        batch = ref_names[start:start + _BATCH]
        embs = model.encode(batch, show_progress_bar=False, batch_size=min(64, len(batch)))
        ref_embs_parts.append(embs)
    ref_embs = np.vstack(ref_embs_parts)
    norms = np.linalg.norm(ref_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    ref_embs /= norms

    query_embs_parts = []
    for start in range(0, len(query_names), _BATCH):
        batch = query_names[start:start + _BATCH]
        embs = model.encode(batch, show_progress_bar=False, batch_size=min(64, len(batch)))
        query_embs_parts.append(embs)
    query_embs = np.vstack(query_embs_parts)
    norms = np.linalg.norm(query_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    query_embs /= norms

    similarities = query_embs @ ref_embs.T

    # Inverse-frequency weighting to counteract class imbalance in the
    # reference set (He & Garcia 2009, "Learning from Imbalanced Data,"
    # IEEE TKDE 21:9).  Without this, over-represented classes like
    # FINANCE dominate kNN votes simply due to population, not semantic
    # proximity.  Each vote is divided by sqrt(class_frequency) — the
    # square root provides a moderate correction that prevents rare
    # classes from becoming over-amplified.
    label_counts = Counter(ref_labels)
    total_refs = len(ref_labels)
    inv_freq: dict[str, float] = {}
    for label, count in label_counts.items():
        inv_freq[label] = 1.0 / (count / total_refs) ** 0.5

    results: dict[str, str] = {}
    for i, name in enumerate(query_names):
        top_k_idx = np.argsort(similarities[i])[-k:][::-1]
        top_k_sims = similarities[i][top_k_idx]

        votes: Counter[str] = Counter()
        for idx, sim in zip(top_k_idx, top_k_sims):
            if sim >= min_similarity:
                label = ref_labels[idx]
                votes[label] += float(sim) * inv_freq.get(label, 1.0)

        if votes:
            results[name] = votes.most_common(1)[0][0]
        else:
            results[name] = "OTHER"

    category_counts = Counter(results.values())
    logger.info(
        "kNN classification complete: %s",
        ", ".join(f"{cat}={cnt}" for cat, cnt in category_counts.most_common(10)),
    )

    return results


# The real industry codes, derived from the config_definitions single source
# of truth minus the three non-industry contribution buckets — so a new
# INDUSTRIES entry is recognized here automatically instead of silently
# dropping until this copy is hand-updated.
VALID_INDUSTRIES = set(INDUSTRIES) - {"SMALL_DONORS", "LARGE_INDIVIDUAL", "UNCLASSIFIED"}

VALID_DONOR_TYPES = {
    "PAC", "Org/Employees", "Party/Ideological", "CandidateAffiliated",
    "Self-Funded", "SKIP",
}


def normalize_learning_store(db_session: Session) -> int:
    """Normalize hallucinated LLM categories and mismatched case to canonical forms.

    Fixes both industry aliases (e.g. SPORTS->MEDIA) and donor type case
    mismatches (e.g. ORG/EMPLOYEES->Org/Employees) that cause kNN vote dilution.

    Also deletes entries with values that can't be mapped to any valid category,
    preventing ghost classes from accumulating.

    Returns the number of rows updated.
    """
    updated = 0
    deleted = 0

    industry_rows = (
        db_session.query(LearnedClassification)
        .filter(LearnedClassification.entity_type == "industry")
        .all()
    )
    for row in industry_rows:
        canonical = _normalize_category(row.value)
        if canonical not in VALID_INDUSTRIES:
            db_session.delete(row)
            deleted += 1
        elif canonical != row.value:
            row.value = canonical
            updated += 1

    dtype_rows = (
        db_session.query(LearnedClassification)
        .filter(LearnedClassification.entity_type == "donor_type")
        .all()
    )
    for row in dtype_rows:
        canonical = normalize_donor_type(row.value)
        if canonical not in VALID_DONOR_TYPES:
            db_session.delete(row)
            deleted += 1
        elif canonical != row.value:
            row.value = canonical
            updated += 1

    if updated or deleted:
        db_session.commit()
        logger.info(
            "Normalized %d learning store entries, deleted %d invalid entries",
            updated, deleted,
        )

    return updated + deleted


def cross_validate_donor_types(db_session: Session) -> int:
    """Re-check PAC-labeled entries against Org/Employees prototype via embedding.

    Companies (LLC, Inc, Bank, etc.) often get mislabeled as PAC in the
    learning store due to early kNN runs. This uses embedding cosine
    similarity to detect entries where the Org/Employees prototype is a
    significantly better match than PAC, and corrects them.

    Returns the number of rows corrected.
    """
    from app.pipeline.analyze.donor_classifier_ai import (
        _get_semantic_type_embeddings,
    )
    from app.pipeline.vector_store import get_embedding_model

    pac_rows = (
        db_session.query(LearnedClassification)
        .filter(
            LearnedClassification.entity_type == "donor_type",
            LearnedClassification.value == "PAC",
        )
        .all()
    )

    if not pac_rows:
        return 0

    model = get_embedding_model()
    type_embs = _get_semantic_type_embeddings()

    org_emb = type_embs.get("Org/Employees")
    pac_emb = type_embs.get("PAC")
    if org_emb is None or pac_emb is None:
        return 0

    names = [r.entity_name for r in pac_rows]
    _BATCH = 256
    all_embs = []
    for start in range(0, len(names), _BATCH):
        batch = names[start:start + _BATCH]
        embs = model.encode(batch, show_progress_bar=False, batch_size=min(64, len(batch)))
        all_embs.append(embs)

    query_embs = np.vstack(all_embs)
    norms = np.linalg.norm(query_embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    query_embs /= norms

    org_scores = query_embs @ org_emb
    pac_scores = query_embs @ pac_emb

    corrected = 0
    _MARGIN = 0.05
    for i, row in enumerate(pac_rows):
        if org_scores[i] > pac_scores[i] + _MARGIN and org_scores[i] > 0.30:
            logger.info(
                "Reclassifying %s: PAC -> Org/Employees (org=%.3f, pac=%.3f)",
                row.entity_name, org_scores[i], pac_scores[i],
            )
            row.value = "Org/Employees"
            corrected += 1

    if corrected:
        db_session.commit()
        logger.info("Cross-validated %d PAC entries to Org/Employees", corrected)

    return corrected
