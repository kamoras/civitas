"""
Adaptive bill classifier using retrieval-augmented few-shot learning.

Replaces hardcoded keyword lists with a growing reference corpus of
labeled examples. Each pipeline run adds classified bills to ChromaDB;
subsequent runs use those as kNN reference examples.

Classification tiers (mirroring donor_classifier_ai.py):
  1. Learning store exact match (by bill_id — instant, highest confidence)
  2. kNN against reference corpus in ChromaDB (fast, generalizes)
  3. Embedding similarity against seed policy descriptions (cold-start fallback)
  4. Every result stored for future reference

Academic rationale
------------------
The tiered approach follows the retrieval-augmented classification (RAC)
pattern described in Lewis et al. (2020, "Retrieval-Augmented Generation
for Knowledge-Intensive NLP Tasks," NeurIPS): instead of training a
parametric classifier, we retrieve similar labeled examples at inference
time and classify by analogy.

kNN in embedding space (tier 2) is grounded in Cover & Hart (1967,
"Nearest Neighbor Pattern Classification," IEEE Trans. Info Theory 13:1).
The similarity-weighted majority vote variant used here is the
distance-weighted kNN from Dudani (1976, "The Distance-Weighted k-Nearest-
Neighbor Rule," IEEE Trans. SMC 6:4), which assigns higher influence to
closer neighbors. k=7 was chosen following the sqrt(N) heuristic common
in the kNN literature (Lall & Sharma 1996).

The learning store acts as an experience replay buffer (Lin 1992,
"Self-Improving Reactive Agents Based on Reinforcement Learning,"
Machine Learning 8:3-4): past classification decisions inform future
ones, improving both speed and accuracy over time.

Seed policy descriptions (tier 3) provide a zero-shot cold-start
fallback using the same nearest-centroid principle as Rocchio
classification (Manning, Raghavan & Schütze 2008, Ch. 14).

References
----------
- Lewis, P. et al. (2020). Retrieval-Augmented Generation for
  Knowledge-Intensive NLP Tasks. NeurIPS 2020.
- Cover, T. & Hart, P. (1967). Nearest Neighbor Pattern
  Classification. IEEE Trans. Info Theory, 13(1), 21-27.
- Dudani, S. (1976). The Distance-Weighted k-Nearest-Neighbor Rule.
  IEEE Trans. SMC, 6(4), 325-327.
- Lin, L.-J. (1992). Self-Improving Reactive Agents. Machine
  Learning, 8(3-4), 293-321.
"""

import json
import logging
from collections import Counter
from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from app.models import LearnedClassification

logger = logging.getLogger(__name__)

ENTITY_BILL_POLICY = "bill_policy"
ENTITY_MOTION_TYPE = "motion_type"

_reference_embs: np.ndarray | None = None
_reference_labels: list[str] = []
_reference_bill_ids: set[str] = set()


def clear_reference_cache() -> None:
    """Clear in-memory reference corpus cache between pipeline runs."""
    global _reference_embs, _reference_labels, _reference_bill_ids
    _reference_embs = None
    _reference_labels = []
    _reference_bill_ids = set()


def _load_reference_corpus() -> tuple[np.ndarray | None, list[str]]:
    """Load the reference corpus from ChromaDB (previously classified bills).

    Returns (embedding_matrix, label_list) or (None, []) if empty.
    The "bills" collection is populated by embed_bills() at the end of
    each pipeline run, so it grows over time.
    """
    global _reference_embs, _reference_labels, _reference_bill_ids
    if _reference_embs is not None:
        return _reference_embs, _reference_labels

    try:
        from app.pipeline.vector_store import get_chroma_client
        client = get_chroma_client()
        collection = client.get_collection(name="bills")

        result = collection.get(
            include=["embeddings", "metadatas"],
            limit=5000,
        )
        if not result or not result["ids"]:
            return None, []

        embs = np.array(result["embeddings"])
        labels = [
            (m.get("policyArea") or "PROCEDURAL")
            for m in result["metadatas"]
        ]
        bill_ids = set(result["ids"])

        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embs = embs / norms

        _reference_embs = embs
        _reference_labels = labels
        _reference_bill_ids = bill_ids

        label_dist = Counter(labels)
        logger.info(
            "Loaded %d reference bills from ChromaDB: %s",
            len(labels),
            ", ".join(f"{k}={v}" for k, v in label_dist.most_common(8)),
        )
        return embs, labels

    except Exception as e:
        logger.debug("No reference corpus available yet: %s", e)
        return None, []


def classify_bill_by_reference(
    text: str,
    k: int = 7,
    min_similarity: float = 0.30,
) -> tuple[str | None, float]:
    """Classify a bill using kNN against the reference corpus.

    Finds the k most similar previously-classified bills and assigns
    the policy area by similarity-weighted majority vote.

    Returns (policy_area, confidence) or (None, 0.0) if insufficient data.
    """
    ref_embs, ref_labels = _load_reference_corpus()
    if ref_embs is None or len(ref_labels) < 5:
        return None, 0.0

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    query_emb = model.encode([text[:500]], show_progress_bar=False)[0]
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm

    similarities = ref_embs @ query_emb
    top_k_idx = np.argsort(similarities)[-k:][::-1]

    votes: Counter[str] = Counter()
    for idx in top_k_idx:
        sim = float(similarities[idx])
        if sim >= min_similarity:
            votes[ref_labels[idx]] += sim

    if not votes:
        return None, 0.0

    best_area, best_weight = votes.most_common(1)[0]
    total_weight = sum(votes.values())
    confidence = best_weight / total_weight if total_weight > 0 else 0.0

    return best_area, confidence


def lookup_exact(db: Session, bill_id: str) -> str | None:
    """Check if this exact bill was classified in a prior run."""
    row = (
        db.query(LearnedClassification)
        .filter(
            LearnedClassification.entity_name == bill_id,
            LearnedClassification.entity_type == ENTITY_BILL_POLICY,
        )
        .first()
    )
    return row.value if row else None


def record_classification(
    db: Session,
    bill_id: str,
    bill_text: str,
    policy_area: str,
    confidence: float,
    source: str,
) -> None:
    """Store a bill classification in the learning store for tracking.

    The ChromaDB reference corpus is populated separately by embed_bills().
    This stores metadata for exact-match lookups and audit trail.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.pipeline.vector_store import get_model_version

    meta = json.dumps({
        "text_prefix": bill_text[:200],
        "confidence": round(confidence, 3),
    })

    stmt = sqlite_insert(LearnedClassification).values(
        entity_name=bill_id,
        entity_type=ENTITY_BILL_POLICY,
        value=policy_area,
        confidence=confidence,
        source=source,
        model_version=get_model_version(),
        match_metadata=meta,
        learned_at=datetime.utcnow(),
    ).on_conflict_do_update(
        index_elements=["entity_name", "entity_type"],
        set_={
            "value": policy_area,
            "confidence": confidence,
            "source": source,
            "match_metadata": meta,
            "learned_at": datetime.utcnow(),
        },
    )
    db.execute(stmt)


def invalidate_thin_classifications(db: Session, min_text_length: int = 40) -> int:
    """Remove learning store entries classified with insufficient text.

    Bills with very short text_prefix (e.g., person-name titles like
    'Jaime's Law') were classified with almost no semantic signal. Now
    that we enrich classification text with official titles and CRS
    policy areas, these stale entries should be removed so the enriched
    text gets a fresh classification.

    Also purges corresponding entries from the ChromaDB reference corpus
    so the kNN classifier doesn't return stale labels.

    Returns the number of invalidated entries.
    """
    rows = (
        db.query(LearnedClassification)
        .filter(
            LearnedClassification.entity_type == ENTITY_BILL_POLICY,
        )
        .all()
    )

    stale_ids: list[str] = []
    for row in rows:
        try:
            meta = json.loads(row.match_metadata or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        text_prefix = meta.get("text_prefix", "")
        if len(text_prefix.strip()) < min_text_length:
            stale_ids.append(row.entity_name)

    if not stale_ids:
        return 0

    db.query(LearnedClassification).filter(
        LearnedClassification.entity_name.in_(stale_ids),
        LearnedClassification.entity_type == ENTITY_BILL_POLICY,
    ).delete(synchronize_session="fetch")
    db.commit()

    _purge_reference_entries(stale_ids)

    logger.info(
        "Invalidated %d thin bill classifications (text < %d chars)",
        len(stale_ids), min_text_length,
    )
    return len(stale_ids)


def _purge_reference_entries(bill_ids: list[str]) -> None:
    """Remove specific entries from the ChromaDB reference corpus."""
    if not bill_ids:
        return
    try:
        from app.pipeline.vector_store import get_chroma_client
        client = get_chroma_client()
        collection = client.get_collection(name="bills")
        existing = collection.get(ids=bill_ids)
        ids_to_delete = [
            bid for bid in existing["ids"]
        ] if existing and existing["ids"] else []
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            logger.info("Purged %d stale entries from reference corpus", len(ids_to_delete))
    except Exception:
        pass


def classify_motion_type(question: str) -> str:
    """Classify a Senate.gov question field as a motion type.

    Instead of hardcoded prefixes, uses embedding similarity against
    known motion type prototypes. Returns the motion type label.

    Motion types:
      "passage"    — vote on the bill itself (substantive)
      "amendment"  — vote on an amendment (may be substantive)
      "cloture"    — procedural mechanism on a substantive bill
      "nomination" — confirmation vote (procedural unless policy-linked)
      "procedural" — pure parliamentary procedure
      "unknown"    — could not classify
    """
    if not question or len(question.strip()) < 5:
        return "unknown"

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    motion_prototypes = _get_motion_prototypes()

    query_emb = model.encode([question[:200]], show_progress_bar=False)[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_type = "unknown"
    best_score = 0.30

    for mtype, emb in motion_prototypes.items():
        score = float(np.dot(query_emb, emb))
        if score > best_score:
            best_score = score
            best_type = mtype

    return best_type


_motion_proto_cache: dict[str, np.ndarray] = {}

MOTION_PROTOTYPES: dict[str, str] = {
    "passage": (
        "On Passage of the Bill. "
        "On the Joint Resolution. On the Concurrent Resolution. "
        "On the Resolution. Shall the bill pass."
    ),
    "amendment": (
        "On the Amendment. On Agreeing to the Amendment. "
        "On the Motion to Concur with Amendment. "
        "On the Substitute Amendment."
    ),
    "cloture": (
        "On the Cloture Motion. Cloture on the Motion to Proceed. "
        "Is it the sense of the Senate that the debate shall be "
        "brought to a close. Invoke cloture."
    ),
    "nomination": (
        "On the Nomination. On the Motion to Confirm. "
        "Confirmation of the nominee. Executive Calendar nomination. "
        "Confirming the appointment."
    ),
    "procedural": (
        "On the Motion to Table. On the Motion to Proceed. "
        "On the Motion to Reconsider. Adjourn. Quorum call. "
        "Reading of the Journal. Sine die. En bloc."
    ),
    "veto": (
        "On Overriding the Veto. On the Veto Message. "
        "Shall the bill pass, the objections of the President notwithstanding."
    ),
}


def _get_motion_prototypes() -> dict[str, np.ndarray]:
    """Compute and cache motion type prototype embeddings."""
    if _motion_proto_cache:
        return _motion_proto_cache

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    for mtype, desc in MOTION_PROTOTYPES.items():
        emb = model.encode([desc], show_progress_bar=False)[0]
        _motion_proto_cache[mtype] = emb / np.linalg.norm(emb)

    return _motion_proto_cache


def get_health_metrics(db: Session) -> dict:
    """Return classification health metrics for monitoring and drift detection.

    Tracks distribution of classifications, confidence levels, and
    source tier usage across pipeline runs.
    """
    rows = (
        db.query(
            LearnedClassification.value,
            LearnedClassification.source,
            LearnedClassification.confidence,
        )
        .filter(LearnedClassification.entity_type == ENTITY_BILL_POLICY)
        .all()
    )

    if not rows:
        return {"total": 0, "message": "No bill classifications stored yet"}

    area_dist = Counter(r[0] for r in rows)
    source_dist = Counter(r[1] for r in rows)
    avg_confidence = sum(r[2] for r in rows) / len(rows)

    return {
        "total": len(rows),
        "policy_area_distribution": dict(area_dist.most_common()),
        "source_distribution": dict(source_dist.most_common()),
        "avg_confidence": round(avg_confidence, 3),
        "reference_corpus_size": len(_reference_labels) if _reference_labels else 0,
    }
