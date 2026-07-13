"""Analyze Senate floor speeches for advocacy alignment with campaign promises.

Purely algorithmic — **zero LLM calls**.  Uses embedding cosine similarity
against policy area descriptions to determine which categories a senator is
actively discussing on the Senate floor.  This captures "effort" that voting
records miss: in a gridlocked Senate, a senator who can't get bills passed
but keeps raising their promised issues on the floor is still trying to
represent their constituents.

The output feeds into the Promise Persistence score as a 15% advocacy bonus.

All classification is embedding-based — no hardcoded keyword lists.  Each
floor remark is embedded and compared against the policy-area anchors
defined in ``policy_alignment.POLICY_ANCHORS`` (which are the same anchors
used for industry↔policy similarity), following the zero-shot nearest-
centroid classification pattern (Rocchio 1971; Reimers & Gurevych 2019).
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

_SIMILARITY_THRESHOLD = 0.70


def _classify_remark_categories(text: str) -> set[str]:
    """Determine which policy category a floor remark relates to via embeddings.

    Returns at most one category — the single best match, if it clears
    the threshold — not "every anchor that scores above the bar." Floor
    remarks are short-register text (ceremonial recognitions, procedural
    announcements, substantive speeches all share the same register),
    and this model's baseline cosine similarity for unrelated formal
    English runs ~0.55-0.87 (see ``compute_promise_vote_alignment``
    docstring in ``policy_alignment.py``). Measured directly on 600 real
    floor speeches: best-anchor score ranges ceremonial junk ("JOANNA
    SHEAF CELEBRATES 100TH BIRTHDAY" 0.51, "CELEBRATING CEREBRAL PALSY
    DAY" 0.52) up through genuinely substantive remarks ("MIGRANTS
    FLEEING RELIGIOUS PERSECUTION" 0.77, "HEALTH INSURANCE COMPANY
    MEDICARE ADVANTAGE DENIALS" 0.76), p50=0.66. At the old threshold
    (0.35, and allowing every anchor above it rather than just the
    best), every single one of 400 sampled real speeches matched all 14
    categories — including a school-recognition speech matching GUNS,
    HEALTHCARE, TAXES, IMMIGRATION, FINANCIAL, ENERGY, JUSTICE, and
    WELFARE. That fed a near-constant ~1.0 "advocacy coverage" into the
    Promise Persistence score's 15%-weighted floor-advocacy component
    for virtually every member with any floor remarks, regardless of
    what they actually said (2026-07 audit). 0.70 keeps ~29% of real
    remarks as classified (matching the ceremonial/substantive split
    visible above), each contributing to at most one category.
    """
    if not text or len(text.strip()) < 30:
        return set()

    from app.pipeline.analyze.policy_alignment import POLICY_ANCHORS, _embed, _embed_batch

    anchor_keys = [k for k in POLICY_ANCHORS if k != "PROCEDURAL"]
    anchor_texts = [POLICY_ANCHORS[k] for k in anchor_keys]

    remark_emb = _embed(text[:500])
    anchor_embs = _embed_batch(anchor_texts)

    if anchor_embs.size == 0:
        return set()

    similarities = anchor_embs @ remark_emb
    best_idx = int(np.argmax(similarities))
    best_sim = float(similarities[best_idx])

    if best_sim >= _SIMILARITY_THRESHOLD:
        return {anchor_keys[best_idx].lower()}
    return set()


def analyze_floor_advocacy(
    senator_remarks: list[dict],
    campaign_promises: list[dict],
) -> dict:
    """Analyze whether a senator's floor remarks align with their campaign promises.

    Args:
        senator_remarks: List of remark dicts from ``parse_speaking_turns``
            (keys: ``date``, ``text``, ``title``).
        campaign_promises: List of promise dicts from platform analysis
            (keys: ``promiseText``, ``category``, ``alignment``, ...).

    Returns:
        Dict with:
            ``advocacyCoverage``: float (0-1), fraction of distinct promise
                categories that have matching floor advocacy evidence.
            ``advocatedCategories``: list of all policy categories discussed
                on the floor (may include categories beyond promises).
            ``totalRemarks``: int, total floor remarks for this senator.
            ``remarksByCategory``: dict mapping category -> remark count.
    """
    if not senator_remarks:
        return {
            "advocacyCoverage": 0.0,
            "advocatedCategories": [],
            "totalRemarks": 0,
            "remarksByCategory": {},
        }

    category_counts: dict[str, int] = {}
    for remark in senator_remarks:
        text = remark.get("text", "")
        title = remark.get("title", "")
        categories = _classify_remark_categories(f"{title} {text}")
        for cat in categories:
            category_counts[cat] = category_counts.get(cat, 0) + 1

    promise_categories: set[str] = set()
    for p in campaign_promises:
        cat = p.get("category", "other")
        if cat != "other":
            promise_categories.add(cat)

    advocated_promise_cats = promise_categories & set(category_counts.keys())
    coverage = (
        len(advocated_promise_cats) / len(promise_categories)
        if promise_categories
        else 0.0
    )

    return {
        "advocacyCoverage": round(coverage, 2),
        "advocatedCategories": sorted(category_counts.keys()),
        "totalRemarks": len(senator_remarks),
        "remarksByCategory": category_counts,
    }
