"""
Bill legislative-stage classification.

Classifies a bill's `latestAction` free text (e.g. "Referred to the
Committee on...", "Passed Senate", "Signed by President") into a stage
along the introduced -> committee -> floor -> other chamber -> president
-> enacted pipeline.

Follows the same embedding-prototype approach as
bill_learning.classify_motion_type: cosine similarity against
natural-language stage descriptions, not keyword/regex matching (see
AGENTS.md's "no hardcoded classification" principle). The valid stage
codes, their display labels, colors, and pipeline order are defined once
in config_definitions.BILL_STAGES and served to the frontend via
/api/config.
"""
import numpy as np

# Natural-language prototypes, one per stage in config_definitions.BILL_STAGES.
# Kept separate from config_definitions because these are classifier
# internals (versioned via the pipeline code hash), not frontend-facing config.
STAGE_PROTOTYPES: dict[str, str] = {
    "INTRODUCED": (
        "Introduced in the House of Representatives. Introduced in the "
        "Senate. Read the first time. Sponsor introductory remarks on "
        "measure."
    ),
    "IN_COMMITTEE": (
        "Referred to the Committee on the subject matter for "
        "consideration. Ordered to be reported by the committee. "
        "Reported by the committee with an amendment. Placed on the "
        "Union Calendar. Placed on the Senate Legislative Calendar. "
        "Committee markup completed. Discharged from committee."
    ),
    "PASSED_CHAMBER": (
        "Passed House. Passed Senate. Passed the House of "
        "Representatives by recorded vote. Passed the Senate by voice "
        "vote. Agreed to in the House. Agreed to in the Senate. On "
        "passage passed. Motion to reconsider laid on the table."
    ),
    "IN_OTHER_CHAMBER": (
        "Received in the Senate after passing the House. Received in "
        "the House after passing the Senate. Held at the desk. Referred "
        "to the other body's committee after passage in the originating "
        "chamber."
    ),
    "TO_PRESIDENT": (
        "Presented to the President. Cleared for White House. Sent to "
        "the President for signature."
    ),
    "ENACTED": (
        "Became Public Law. Signed by the President. Enacted into law."
    ),
    "VETOED": (
        "Vetoed by the President. Veto message received in the House. "
        "Pocket veto."
    ),
}

_stage_proto_cache: dict[str, np.ndarray] = {}

_FALLBACK_STAGE = "INTRODUCED"
_MIN_SCORE = 0.30


def _get_stage_prototypes() -> dict[str, np.ndarray]:
    """Compute and cache stage prototype embeddings."""
    if _stage_proto_cache:
        return _stage_proto_cache

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    for stage, desc in STAGE_PROTOTYPES.items():
        emb = model.encode([desc], show_progress_bar=False)[0]
        _stage_proto_cache[stage] = emb / np.linalg.norm(emb)

    return _stage_proto_cache


def classify_bill_stage(latest_action: str, is_law: bool = False) -> str:
    """Classify a bill's latest action text into a legislative stage.

    `is_law` is a hard fact (congress.gov's own "Became Public Law"
    marker) rather than a fuzzy classification, so it short-circuits to
    ENACTED without needing an embedding comparison.
    """
    if is_law:
        return "ENACTED"

    if not latest_action or len(latest_action.strip()) < 3:
        return _FALLBACK_STAGE

    from app.pipeline.vector_store import get_embedding_model
    model = get_embedding_model()

    prototypes = _get_stage_prototypes()
    query_emb = model.encode(
        [latest_action[:300]], prompt_name="query", show_progress_bar=False,
    )[0]
    query_emb = query_emb / np.linalg.norm(query_emb)

    best_stage = _FALLBACK_STAGE
    best_score = _MIN_SCORE
    for stage, emb in prototypes.items():
        score = float(np.dot(query_emb, emb))
        if score > best_score:
            best_score = score
            best_stage = stage

    return best_stage


def clear_stage_embedding_cache() -> None:
    """Clear cached prototype embeddings (e.g. between test runs)."""
    _stage_proto_cache.clear()
