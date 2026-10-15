"""Calibrate post_composer's acting-party vote (kNN over labelled spans).

Writes app/data/claim_actor_calibration.json: the k and vote threshold
post_composer.is_acting_party uses for spans its capitalised-word rule
refuses, the hash of the labelled set it was fitted to, and the
leave-one-out measurement. Rerun when app/data/claim_actor_examples.json
changes, or when the similarity model does (tests/test_post_composer.py
fails until the hash matches).

Method. Each labelled span's vote is the similarity-weighted share of actors
among its k nearest OTHER labelled spans (post_composer.actor_votes) — leave
one out, with every spelling of the same span left out together, since the
model is case-blind and a span's own capitalised twin would vote for it.

The choice is constrained, not just scored. A span is admitted when the rule
admits it (post_composer._looks_like_an_actor), or when no pronoun leads it
(post_composer._names_no_one) and its vote clears the threshold. Among the
(k, threshold) settings under which the vote admits NO labelled non-actor the
rule refused — so the two tiers are at least as precise as the rule alone —
the one with the highest recall wins; ties go to the higher threshold, then
the larger k. The same search is run for the vote
ALONE (no rule), and its best setting is recorded as "vote_alone": that is
the measurement behind keeping the rule. Precision, recall and the vote's
AUC are reported with 95% bootstrap intervals, over all spans and over the
replayed production spans alone.

Usage (loads all-MiniLM-L6-v2; no network once the model is cached):
    python backend/scripts/calibrate_claim_actor.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline.analyze.post_composer import (  # noqa: E402
    ACTOR_CALIBRATION_PATH,
    _looks_like_an_actor,
    _names_no_one,
    actor_examples,
    actor_examples_hash,
    actor_votes,
    embed_spans,
)

K_GRID = (5, 9, 15, 25)
THRESHOLD_GRID = np.round(np.arange(0.40, 0.951, 0.05), 2)
BOOTSTRAP = 4000


def leave_one_out_votes(embeddings: np.ndarray, labels: np.ndarray, texts: list[str], k: int) -> np.ndarray:
    keys = np.array([t.lower() for t in texts])
    votes = np.empty(len(texts))
    for i in range(len(texts)):
        others = keys != keys[i]
        votes[i] = actor_votes(embeddings[i:i + 1], embeddings[others], labels[others], k)[0]
    return votes


def auc(scores: np.ndarray, truth: np.ndarray) -> float:
    pos, neg = scores[truth], scores[~truth]
    wins = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(wins / max(len(pos) * len(neg), 1))


def precision_recall(truth: np.ndarray, kept: np.ndarray) -> tuple[float, float]:
    tp = float(np.sum(truth & kept))
    return tp / max(kept.sum(), 1), tp / max(truth.sum(), 1)


def interval(truth: np.ndarray, kept: np.ndarray, scores: np.ndarray | None = None) -> dict:
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(BOOTSTRAP):
        i = rng.integers(0, len(truth), len(truth))
        row = list(precision_recall(truth[i], kept[i]))
        if scores is not None:
            row.append(auc(scores[i], truth[i]))
        draws.append(row)
    lo, hi = np.percentile(np.array(draws), [2.5, 97.5], axis=0)
    p, r = precision_recall(truth, kept)
    out = {
        "spans": int(len(truth)),
        "admitted": int(kept.sum()),
        "precision": [round(p, 3), round(lo[0], 3), round(hi[0], 3)],
        "recall": [round(r, 3), round(lo[1], 3), round(hi[1], 3)],
    }
    if scores is not None:
        out["auc"] = [round(auc(scores, truth), 3), round(lo[2], 3), round(hi[2], 3)]
    return out


def best_setting(votes: dict, labels: np.ndarray, rule: np.ndarray, voted: np.ndarray, with_rule: bool):
    """(recall, threshold, k) of the best setting meeting the constraint, or
    None. `voted` marks the spans the vote may admit."""
    allowed = []
    rule_precision, _ = precision_recall(labels, rule)
    for k, v in votes.items():
        for t in THRESHOLD_GRID:
            kept = ((v >= t) & voted) | rule if with_rule else v >= t
            precision, recall = precision_recall(labels, kept)
            if not np.any(kept & ~labels & ~rule) and precision >= rule_precision:
                allowed.append((recall, float(t), k))
    return max(allowed) if allowed else None


def main() -> None:
    examples = actor_examples()
    texts = [e["text"] for e in examples]
    labels = np.array([e["actor"] for e in examples])
    rule = np.array([_looks_like_an_actor(t) for t in texts])
    voted = ~np.array([_names_no_one(t) for t in texts])
    replay = np.array([e["origin"] == "replay" for e in examples])
    embeddings = embed_spans(texts)
    votes = {k: leave_one_out_votes(embeddings, labels.astype(float), texts, k) for k in K_GRID}

    chosen = best_setting(votes, labels, rule, voted, with_rule=True)
    if chosen is None:
        raise SystemExit("No vote setting meets the constraint; keep the rule alone.")
    _, threshold, k = chosen
    kept = rule | ((votes[k] >= threshold) & voted)
    alone = best_setting(votes, labels, rule, voted, with_rule=False)
    vote_alone = None
    if alone is not None:
        _, alone_t, alone_k = alone
        alone_kept = votes[alone_k] >= alone_t
        vote_alone = {
            "k": alone_k,
            "threshold": alone_t,
            "all": interval(labels, alone_kept),
            "replay": interval(labels[replay], alone_kept[replay]),
            "rule_admitted_actors_refused": [int(np.sum(~alone_kept & labels & rule)), int(np.sum(labels & rule))],
            "replay_rule_admitted_actors_refused": sorted(
                texts[i] for i in np.flatnonzero(replay & labels & rule & ~alone_kept)
            ),
        }

    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_claim_actor.py from "
            "app/data/claim_actor_examples.json: k and the vote threshold for the "
            "similarity-weighted kNN acting-party vote (all-MiniLM-L6-v2) that "
            "post_composer applies to spans its capitalised-word rule refuses (unless a "
            "pronoun leads them). Chosen as "
            "the highest leave-one-out recall (rule OR vote) among settings under which "
            "the vote admits no labelled non-actor the rule refused. 'vote_alone' is the "
            "same search with no rule. Figures are leave-one-out, as [value, 2.5%, 97.5%] "
            "bootstrap intervals; 'replay' is the production spans alone (2026-10-11..15 "
            "claim calls)."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "examples_hash": actor_examples_hash(),
        "examples": len(examples),
        "k": k,
        "threshold": threshold,
        "vote_auc": {
            "all": interval(labels, kept, votes[k])["auc"],
            "replay": interval(labels[replay], kept[replay], votes[k][replay])["auc"],
        },
        "rule_or_vote": {
            "all": interval(labels, kept),
            "replay": interval(labels[replay], kept[replay]),
        },
        "rule_alone": {
            "all": interval(labels, rule),
            "replay": interval(labels[replay], rule[replay]),
        },
        "rule_refused_actors_admitted": [int(np.sum(kept & labels & ~rule)), int(np.sum(labels & ~rule))],
        "replay_rule_refused_actors_admitted": sorted(
            texts[i] for i in np.flatnonzero(replay & labels & ~rule & kept)
        ),
        "vote_alone": vote_alone,
    }
    ACTOR_CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    for name, mask in (
        ("actors refused", labels & ~kept),
        ("non-actors admitted", ~labels & kept),
    ):
        print(f"\n{name}:", ", ".join(f"{texts[i]!r} {votes[k][i]:.2f}" for i in np.flatnonzero(mask)))


if __name__ == "__main__":
    main()
