"""Commemorative bill detection — Volden & Wiseman's lowest significance tier.

V&W's Legislative Effectiveness Score weights a bill by what it does:
commemorative 1x (naming a post office or federal building, awarding a
Congressional Gold Medal, recognising a person or event), substantive 5x.
Bill type cannot tell them apart — a post-office naming is an H.R. bill like
any other — so Legislative Effectiveness used to weight it 5x. Against V&W's
published scores, content-based commemorative weighting is worth House rank
agreement 0.93 -> 0.98 (scripts/research_les_stage_weighting.py).

Zero-shot embedding classification (AGENTS.md principle 1): a title's best
cosine similarity to the commemorative prototypes minus its similarity to
the substantive prototype, compared with a threshold. The threshold is
calibrated data, not a literal (§3a): scripts/calibrate_commemorative.py
fits it against V&W's per-member commemorative counts for the 118th
Congress and writes app/data/commemorative_calibration.json, which records
a hash of the prototypes it was fitted to (a test fails if they drift).

Runs on the similarity model (all-MiniLM-L6-v2), the one the calibration
measured. Pipeline-only: the result is stored per bill, so scoring,
breakdowns and the startup rescore never load a model.
"""

import hashlib
import json
import logging
import pathlib

import numpy as np

logger = logging.getLogger(__name__)

COMMEMORATIVE_PROTOTYPES: tuple[str, ...] = (
    "Designates the name of a post office, federal building, courthouse or other facility after a person.",
    "Awards a Congressional Gold Medal to honor a person or group.",
    "Commemorates or recognizes a person, anniversary, or event.",
)
SUBSTANTIVE_PROTOTYPES: tuple[str, ...] = (
    "Changes federal law, programs, funding, taxes, regulation or agency authority.",
)

_CALIBRATION_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "commemorative_calibration.json"
_calibration_cache: dict | None = None
# Prototype embeddings per encoder (keyed by id): vectors from one model
# are meaningless against another's. The entry holds the encoder itself so
# its id can't be recycled for a different object while the entry exists
# (an id is only unique among live objects), and the lookup checks identity.
_prototype_cache: dict[int, tuple[object, np.ndarray, np.ndarray]] = {}


def prototype_hash() -> str:
    """Identifies the prototype text a calibration was fitted to."""
    text = "\n".join(COMMEMORATIVE_PROTOTYPES) + "\n--\n" + "\n".join(SUBSTANTIVE_PROTOTYPES)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def calibration() -> dict:
    global _calibration_cache
    if _calibration_cache is None:
        _calibration_cache = json.loads(_CALIBRATION_PATH.read_text())
    return _calibration_cache


def commemorative_margins(titles: list[str], model) -> np.ndarray:
    """Best commemorative-prototype similarity minus substantive similarity,
    per title. `model` is a SentenceTransformer-compatible encoder."""
    entry = _prototype_cache.get(id(model))
    if entry is None or entry[0] is not model:
        entry = (
            model,
            model.encode(list(COMMEMORATIVE_PROTOTYPES), normalize_embeddings=True, show_progress_bar=False),
            model.encode(list(SUBSTANTIVE_PROTOTYPES), normalize_embeddings=True, show_progress_bar=False),
        )
        _prototype_cache[id(model)] = entry
    _, commem, subst = entry
    emb = model.encode(list(titles), normalize_embeddings=True, show_progress_bar=False)
    return (emb @ commem.T).max(axis=1) - (emb @ subst.T).max(axis=1)


def classify_commemorative(titles: list[str], model=None) -> list[bool]:
    """True for each title that reads as commemorative."""
    if not titles:
        return []
    if model is None:
        from app.pipeline.vector_store import get_similarity_model
        model = get_similarity_model()
    threshold = float(calibration()["threshold"])
    return [bool(m > threshold) for m in commemorative_margins(titles, model)]


def mark_commemorative(bills: list[dict]) -> None:
    """Set `commemorative` on each sponsored-bill dict in place, from its
    title. Best-effort: on failure every bill stays unflagged (weighted as
    substantive, the pre-v6.14 behaviour) and the reason is logged."""
    candidates = [b for b in bills if (b.get("title") or "").strip()]
    try:
        flags = classify_commemorative([b["title"] for b in candidates])
    except Exception:
        logger.warning("Commemorative classification failed — bills weighted as substantive", exc_info=True)
        return
    for bill, flag in zip(candidates, flags):
        bill["commemorative"] = flag
