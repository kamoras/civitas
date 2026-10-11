"""Calibrate the Action Center's article relevance gate (kNN vote).

Writes app/data/policy_relevance_calibration.json: the k and vote threshold
action_center._filter_policy_relevant uses, the hash of the labelled set it
was fitted to, and the held-out measurement. Rerun when
app/data/policy_relevance_examples.json changes, or when the similarity
model does (tests/test_action_center.py fails until the hash matches).

Method. Each article's vote is the similarity-weighted share of relevant
examples among its k nearest labelled articles (relevance_votes). Choosing
k and the threshold on the same articles they are scored on would reward
memorising the week's stories — one event is covered by several outlets —
so the fit is temporal: for each cut, the reference set is every example
dated before it, and the test set is the feed articles dated a week or more
after it. The score is mean F0.5 over the cuts: the Action Center's aim is
fewer, better issues, so a false keep costs more than a false drop. Among
the settings within one standard error of the best, the lowest threshold
wins (see main for why). The reported figures are those held-out scores, with 95%
bootstrap intervals. Test articles are feed articles only — the 'clustered'
sample is articles production had already kept, which would overstate
precision.

Usage (loads all-MiniLM-L6-v2; no network once the model is cached):
    python backend/scripts/calibrate_policy_relevance.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline.analyze.action_center import (  # noqa: E402
    _RELEVANCE_CALIBRATION_PATH,
    _embed_texts_sim,
    relevance_examples,
    relevance_examples_hash,
    relevance_votes,
)

K_GRID = (5, 9, 15, 25, 41)
THRESHOLD_GRID = np.round(np.arange(0.40, 0.951, 0.05), 2)
# Each cut leaves a week out between reference and test, so a story the
# reference set saw is not still running in the test set.
GAP_DAYS = 7
CUT_QUANTILES = (0.35, 0.5, 0.65)
BOOTSTRAP = 4000


def prf(truth: np.ndarray, kept: np.ndarray, beta: float = 1.0) -> tuple[float, float, float]:
    tp = float(np.sum(truth & kept))
    precision = tp / max(kept.sum(), 1)
    recall = tp / max(truth.sum(), 1)
    b2 = beta * beta
    f = (1 + b2) * precision * recall / max(b2 * precision + recall, 1e-12)
    return precision, recall, f


def interval(truth: np.ndarray, kept: np.ndarray) -> dict:
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(BOOTSTRAP):
        i = rng.integers(0, len(truth), len(truth))
        draws.append(prf(truth[i], kept[i]))
    lo, hi = np.percentile(np.array(draws), [2.5, 97.5], axis=0)
    p, r, f = prf(truth, kept)
    return {
        "articles": int(len(truth)),
        "precision": [round(p, 3), round(lo[0], 3), round(hi[0], 3)],
        "recall": [round(r, 3), round(lo[1], 3), round(hi[1], 3)],
        "f1": [round(f, 3), round(lo[2], 3), round(hi[2], 3)],
        "f0_5": round(prf(truth, kept, 0.5)[2], 3),
    }


def main() -> None:
    examples = relevance_examples()
    embeddings = _embed_texts_sim([e["text"] for e in examples])
    labels = np.array([e["relevant"] for e in examples])
    dates = np.array([e["date"] for e in examples])
    feed = np.array([e["sample"] == "feed" for e in examples])

    ordered = np.sort(dates[feed])
    splits = []
    for q in CUT_QUANTILES:
        cut = ordered[int(q * len(ordered))]
        gap_end = (datetime.date.fromisoformat(cut) + datetime.timedelta(days=GAP_DAYS)).isoformat()
        splits.append((cut, gap_end, dates < cut, feed & (dates >= gap_end)))

    def votes_for(k: int, reference: np.ndarray, test: np.ndarray) -> np.ndarray:
        return relevance_votes(embeddings[test], embeddings[reference], labels[reference].astype(float), k)

    scores = {}
    split_votes = {k: [votes_for(k, ref, test) for _, _, ref, test in splits] for k in K_GRID}
    for k in K_GRID:
        for t in THRESHOLD_GRID:
            scores[(k, float(t))] = np.mean([
                prf(labels[test], v >= t, 0.5)[2]
                for (_, _, _, test), v in zip(splits, split_votes[k])
            ])
    best_k, best_t = max(scores, key=scores.get)
    # One-standard-error rule (Breiman et al. 1984): F0.5 is flat near its
    # peak, and article-level F0.5 can't see that a high threshold drops
    # every article of a story the reference set hasn't met yet (a U.S.
    # story whose nearest examples are foreign coverage of the same place).
    # So among settings within one bootstrap standard error of the best,
    # take the lowest threshold, then the largest k.
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(BOOTSTRAP // 4):
        per_split = []
        for (_, _, _, test), v in zip(splits, split_votes[best_k]):
            i = rng.integers(0, int(test.sum()), int(test.sum()))
            per_split.append(prf(labels[test][i], (v >= best_t)[i], 0.5)[2])
        draws.append(np.mean(per_split))
    se = float(np.std(draws))
    near = [kt for kt, s in scores.items() if s >= scores[(best_k, best_t)] - se]
    k, threshold = min(near, key=lambda kt: (kt[1], -kt[0]))

    held_out = {}
    pooled_truth, pooled_kept = [], []
    for cut, gap_end, ref, test in splits:
        kept = votes_for(k, ref, test) >= threshold
        held_out[f"reference < {cut}, test >= {gap_end}"] = interval(labels[test], kept)
        pooled_truth.append(labels[test])
        pooled_kept.append(kept)
    held_out["pooled"] = interval(np.concatenate(pooled_truth), np.concatenate(pooled_kept))

    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_policy_relevance.py from "
            "app/data/policy_relevance_examples.json: k and the vote threshold for "
            "the similarity-weighted kNN relevance vote (all-MiniLM-L6-v2), chosen "
            "to maximise mean F0.5 over temporal splits (reference = examples before "
            f"the cut, test = feed articles {GAP_DAYS}+ days after it). held_out holds "
            "those test scores as [value, 2.5%, 97.5%] bootstrap intervals."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "examples_hash": relevance_examples_hash(),
        "examples": len(examples),
        "k": k,
        "threshold": threshold,
        "best_f0_5": {"k": best_k, "threshold": best_t, "mean_f0_5": round(scores[(best_k, best_t)], 3),
                      "standard_error": round(se, 3)},
        "chosen_mean_f0_5": round(scores[(k, threshold)], 3),
        "held_out": held_out,
    }
    _RELEVANCE_CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
