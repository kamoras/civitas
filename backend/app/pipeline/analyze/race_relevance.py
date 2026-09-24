"""Is this story ABOUT this race? — a semantic gate, not a string gate.

election_coverage.py attaches a story to a race by name match, which
answers "does this text mention a candidate" and cannot answer "is this
text about that candidate's race". Those are different questions, and
the gap between them is what put a Paramount/Warner Bros. merger story
on TN-9, a Tupac trial on NC-1 and Shakespeare's King John on GA-SEN.

A name match is the right primitive for ATTRIBUTION — it is exact, and
election_coverage's docstring explains why a fuzzy match must never
decide which candidate a scandal belongs to. It is the wrong primitive
for RELEVANCE, which is a question about meaning. This module answers
that second question with the embedding stack already in the codebase,
and leaves attribution alone.

Why an embedding and not more patterns
--------------------------------------
Relevance is unbounded: there is no finite list of ways for an article
to be about an election, which is exactly why the hand-written filters
tried before this failed. Two were measured against the live corpus and
rejected: electoral-vocabulary matching dropped a real Roll Call piece
on the CT-1 primary while keeping three aggregator reposts, and
domain-verified handles turned out to be mostly weather bots. Cosine
similarity in a sentence-embedding space is the standard tool for
graded semantic relatedness and degrades gracefully — a borderline miss
costs one post, which is the right cost profile for a ranking decision.

Safety decisions are deliberately NOT made here. A classifier has a
false-negative rate, and one miss on an endorsement is another "VOTE
VERONICA FERNANDEZ" on a non-partisan account. Endorsements and invented
facts are made impossible structurally in post_composer.py; this module
only decides what is worth saying at all.

The threshold is derived, never typed
-------------------------------------
Otsu's method (Otsu 1979, "A Threshold Selection Method from Gray-Level
Histograms", IEEE Trans. SMC 9:1) picks the cut that maximises
between-class variance in a bimodal distribution. It takes no parameter
and no labels: the corpus decides. Measured over 800 live matched items
it lands at 0.276, splitting "Democrat John Larson loses to younger
primary challenger" (0.663) and "Senate ballot in Alaska will feature
two Dan Sullivans" (0.614) from "Why does this dog look like Mitch
McConnell?" and a Saskatchewan travel post (both ~0.24).

Same generated-tunable discipline as explore_ranking.py, including its
failure mode: if recalibration fails the previous value stays in force,
because a stale threshold gates better than none.
"""

import json
import logging

logger = logging.getLogger(__name__)

_CACHE_NAMESPACE = "race_relevance"
_CACHE_KEY = "calibration"

# Used only until the first calibration runs. Not a guess: it is the
# Otsu value measured over 800 live items on 2026-09-23, recorded here
# so a fresh database gates sensibly on its first night and is replaced
# by that database's own calibration on the first election run.
BOOTSTRAP_THRESHOLD = 0.276

# Enough of an article to characterise it; beyond this the tail adds
# noise rather than topic, and the encoder truncates anyway.
_TEXT_CHARS = 600

_cached: dict | None = None


def reset_cache() -> None:
    global _cached
    _cached = None


def race_descriptor(race) -> str:
    """What this race IS, in the same register as a news lede — the text
    an article about it should sit near in embedding space."""
    chamber = "U.S. Senate" if race.office == "S" else "U.S. House of Representatives"
    seat = "" if race.office == "S" else f" district {race.district or 'at-large'}"
    names = ", ".join(sorted({c.name for c in race.candidates})[:8])
    descriptor = f"{race.state} {chamber}{seat} election campaign"
    return f"{descriptor}. Candidates: {names}" if names else descriptor


def item_text(item) -> str:
    return f"{item.title or ''} {item.summary or ''}".strip()[:_TEXT_CHARS]


def score_pairs(texts: list[str], descriptors: list[str]) -> list[float]:
    """Cosine similarity per (text, descriptor) pair, both L2-normalised.

    Uses the SIMILARITY model, not the classification one: vector_store's
    own docstring records that the primary model packs all same-register
    text into a narrow band, which is precisely what defeats a threshold.
    """
    if not texts:
        return []
    from app.pipeline.vector_store import encode_normalized, get_similarity_model

    model = get_similarity_model()
    left = encode_normalized(model, texts)
    right = encode_normalized(model, descriptors)
    return [float(v) for v in (left * right).sum(axis=1)]


def otsu_threshold(values: list[float], bins: int = 256) -> float | None:
    """The between-class-variance-maximising cut (Otsu 1979)."""
    import numpy as np

    arr = np.asarray(values, dtype=float)
    if arr.size < bins:
        return None  # too few points for the histogram to mean anything
    hist, edges = np.histogram(arr, bins=bins)
    weights = hist / hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2.0
    best_t, best_var = None, -1.0
    for i in range(1, bins):
        w0, w1 = weights[:i].sum(), weights[i:].sum()
        if w0 == 0 or w1 == 0:
            continue
        mu0 = float((weights[:i] * centers[:i]).sum() / w0)
        mu1 = float((weights[i:] * centers[i:]).sum() / w1)
        between = w0 * w1 * (mu0 - mu1) ** 2
        if between > best_var:
            best_var, best_t = between, float(centers[i])
    return best_t


def threshold(db=None) -> float:
    global _cached
    if _cached is None and db is not None:
        from app.pipeline.cache import api_cache_get

        raw = api_cache_get(db, _CACHE_NAMESPACE, _CACHE_KEY)
        if raw:
            try:
                _cached = json.loads(raw)
            except ValueError:
                logger.warning("Race-relevance calibration unreadable — using bootstrap")
    if _cached:
        return float(_cached.get("threshold", BOOTSTRAP_THRESHOLD))
    return BOOTSTRAP_THRESHOLD


def is_relevant(item, race, db=None) -> bool:
    text = item_text(item)
    if not text:
        return False
    scores = score_pairs([text], [race_descriptor(race)])
    return bool(scores) and scores[0] >= threshold(db)


def calibrate_and_store(db) -> dict | None:
    """Re-derive the threshold from the current corpus and persist it.

    Returns None — leaving the previous calibration in force — when the
    corpus is too small to threshold or the computation fails, for the
    same reason explore_ranking.calibrate_and_store does: a stale
    threshold gates better than no threshold.
    """
    from app.models import Race, RaceCoverageItem
    from app.pipeline.cache import api_cache_set

    try:
        items = (
            db.query(RaceCoverageItem)
            .filter(RaceCoverageItem.match_basis == "full_name")
            .limit(2000)
            .all()
        )
        races = {r.id: r for r in db.query(Race).all()}
        pairs = [(it, races[it.race_id]) for it in items if it.race_id in races]
        pairs = [(it, r) for it, r in pairs if item_text(it)]
        if len(pairs) < 256:
            logger.info("Race-relevance calibration skipped — only %d items", len(pairs))
            return None

        scores = score_pairs(
            [item_text(it) for it, _ in pairs],
            [race_descriptor(r) for _, r in pairs],
        )
        cut = otsu_threshold(scores)
        if cut is None:
            return None
        payload = {"threshold": round(cut, 4), "sample_size": len(scores)}
    except Exception:
        logger.exception("Race-relevance calibration failed — keeping the previous one")
        return None

    api_cache_set(db, _CACHE_NAMESPACE, _CACHE_KEY, json.dumps(payload))
    db.commit()
    reset_cache()
    logger.info(
        "Recalibrated race relevance: threshold=%.4f over %d items",
        payload["threshold"], payload["sample_size"],
    )
    return payload
