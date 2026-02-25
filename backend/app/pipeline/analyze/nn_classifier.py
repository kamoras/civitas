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

from app.models import LearnedClassification

logger = logging.getLogger(__name__)

CATEGORY_ALIASES: dict[str, str] = {
    "LEGAL": "LAWYERS",
    "LEGAL_SERVICES": "LAWYERS",
    "LEGAL_PROFESSIONAL": "LAWYERS",
    "REALESTATE": "REAL_ESTATE",
    "TELECOMMUNICATIONS": "TELECOM",
    "TRANSPORTATION": "TRANSPORT",
    "SCIENCE/INFORMATION_TECH": "TECH",
    "AUTO_MANUFACTURING": "MANUFACTURING",
    "METAL_PLASTIC_AND_COMPOSITES": "MANUFACTURING",
    "WHOLESALE": "RETAIL",
    "AGRICULTURE": "AGRIBUSINESS",
    "AGRICULTURE/COMMODITIES": "AGRIBUSINESS",
    "FISHING_FOOD": "AGRIBUSINESS",
    "FOODS": "AGRIBUSINESS",
    "MEDICAL": "HEALTHCARE",
    "MINING": "ENERGY",
    "MINING/ENERGY": "ENERGY",
    "PETRO_SHELL": "OIL_GAS",
    "RESEARCH": "EDUCATION",
    "UNKNOWN": "OTHER",
    "": "OTHER",
}

DONOR_TYPE_PROTOTYPES: dict[str, str] = {
    "PAC": "political action committee PAC campaign contributions political fund",
    "Org/Employees": "corporation company employees business organization employer firm group",
    "Party/Ideological": "democratic republican party committee national senatorial congressional club growth",
    "CandidateAffiliated": "candidate senator representative campaign friends of committee elect",
    "SKIP": "payment processor earmark conduit WinRed ActBlue Anedot joint fundraising",
}


def _get_model():
    from app.pipeline.vector_store import get_embedding_model
    return get_embedding_model()


def _normalize_category(value: str) -> str:
    v = value.strip().upper()
    return CATEGORY_ALIASES.get(v, v)


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

    results: dict[str, str] = {}
    for i, name in enumerate(query_names):
        top_k_idx = np.argsort(similarities[i])[-k:][::-1]
        top_k_sims = similarities[i][top_k_idx]

        votes: Counter[str] = Counter()
        for idx, sim in zip(top_k_idx, top_k_sims):
            if sim >= min_similarity:
                votes[ref_labels[idx]] += float(sim)

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


def normalize_learning_store(db_session: Session) -> int:
    """Normalize hallucinated LLM categories to canonical ones.

    Returns the number of rows updated.
    """
    rows = (
        db_session.query(LearnedClassification)
        .filter(LearnedClassification.entity_type == "industry")
        .all()
    )

    updated = 0
    for row in rows:
        canonical = _normalize_category(row.value)
        if canonical != row.value:
            row.value = canonical
            updated += 1

    if updated:
        db_session.commit()
        logger.info("Normalized %d learning store entries to canonical categories", updated)

    return updated
