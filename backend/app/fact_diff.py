"""Which of an ActionIssue's current facts are new since its last genuine
content change — the signal behind the reader-facing "new" marker.

A brand-new issue (never updated) has nothing to diff against — every fact
being "new" would just mean the issue itself is new, not that anything
changed since a reader might have last seen it, so that case is left to the
caller to suppress (`stored_new_facts`).

Computed when an issue's facts are written (the hourly Action Center refresh
and the election-night count issues), in the pipeline process, and stored on
the issue (`ActionIssue.new_facts`). The API only reads that column: the diff
needs the embedding model, which a read request must never load.
"""

import json

import numpy as np

from app.pipeline.vector_store import get_similarity_model

# Live report (2026-08-22): "all the key facts always show as new." The LLM
# rewords a claim on nearly every regeneration (same root cause as
# action_center.py's _NEAR_IDENTICAL_TITLE_THRESHOLD bug — a claim never
# survives byte-for-byte between updates), so exact-text matching alone
# almost never suppressed anything.
#
# Measured against 7 real production issues' actual (facts, previous_facts)
# pairs: genuine rewordings of the same claim scored 0.709-1.000 cosine
# (e.g. "faced targeted actions during her tenure" -> "...related to her
# identity during her tenure" at 0.935), but that band overlaps with at
# least one genuinely DIFFERENT claim at 0.764 ("the decision followed
# months of discussions..." vs "the National Trust was involved in the
# dispute" — related components of a restructured narrative, not the same
# claim). There is no clean gap here the way title-matching had one. 0.80
# sits above that overlap entirely, so it trades missing some genuine
# rewordings (they still show as "new" — cosmetic, not a real problem) for
# never wrongly suppressing a genuinely new claim — the platform's
# transparency mission favors that direction of error.
_FACT_REWORDING_THRESHOLD = 0.80


def new_facts_since(current_facts: list[str], previous_facts: list[str]) -> list[str]:
    """Facts in `current_facts` that aren't just a reworded restatement of
    something already in `previous_facts`.

    Order preserved from `current_facts`. Exact-text membership is checked
    first — free, and catches the common case of a fact carried over
    unchanged — before falling back to embedding similarity (see
    _FACT_REWORDING_THRESHOLD) for whatever's left, so a claim the LLM
    merely reworded between generations doesn't read as new information.
    """
    previous = set(previous_facts)
    remaining = [f for f in current_facts if f not in previous]
    if not remaining or not previous_facts:
        return remaining

    embs = np.array(_embed(remaining + previous_facts))
    cur_embs, prev_embs = embs[: len(remaining)], embs[len(remaining):]
    sims = cur_embs @ prev_embs.T
    return [f for f, row in zip(remaining, sims) if row.max() < _FACT_REWORDING_THRESHOLD]


def _embed(texts: list[str]) -> np.ndarray:
    """Normalized embeddings under the similarity model, the model
    _FACT_REWORDING_THRESHOLD was measured on."""
    return get_similarity_model().encode(texts, show_progress_bar=False, normalize_embeddings=True)


def stored_new_facts(facts_json: str | None, previous_json: str | None) -> str:
    """The value to store in ActionIssue.new_facts for these facts: a JSON
    list. An issue never updated (no previous facts) has none, since every
    fact being "new" would only mean the issue itself is new."""
    previous = json.loads(previous_json or "[]")
    if not previous:
        return "[]"
    return json.dumps(new_facts_since(json.loads(facts_json or "[]"), previous))


def stored_exact_new_facts(facts_json: str | None, previous_json: str | None) -> str:
    """stored_new_facts for facts built from a fixed template (the
    election-night count issues): a template can't reword a claim, so a
    fact is new exactly when its text is, and no model is needed."""
    previous = set(json.loads(previous_json or "[]"))
    if not previous:
        return "[]"
    return json.dumps([f for f in json.loads(facts_json or "[]") if f not in previous])
