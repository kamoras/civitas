"""Calibrate the Action Center's committee routing.

Writes app/data/committee_routing_calibration.json: the kNN size k, the
prototype weight alpha, the softmax temperature and the threshold that
app/committee_routing.route_committees uses, the hash of the
labelled issues and prototypes it was fitted to, and the held-out
measurement. Rerun when app/data/committee_routing_examples.json or
app/data/committee_jurisdictions.json changes (the test fails until the hash
matches).

What is measured. Each (issue, chamber) is one decision: name a committee or
abstain. Precision = named committees that are among the issue's labelled
committees in that chamber / committees named. Recall = issues with a
labelled committee in that chamber whose named committee is one of them /
issues with a labelled committee in that chamber. Naming a committee for a
story no committee handles counts against precision.

Method. Temporal: the issues dated before the cut are the training set, the
issues GAP_DAYS or more after it the held-out set, so a story still running
on both sides of the cut can't be scored by its own earlier labels. The
setting is chosen on the training set alone, each training issue scored
against the other training issues more than EXCLUDE_DAYS away from it (one
story runs for days under different titles), by F0.5 — a wrong committee
misleads, an abstention shows nothing. The held-out issues are then scored
against every training issue with that setting. alpha is never 0: the
prototypes always take part; "prototypes_only" reports alpha = 1 alone.

Usage (loads all-MiniLM-L6-v2; no network once the model is cached):
    python backend/scripts/calibrate_committee_routing.py
"""

from __future__ import annotations

import datetime
import itertools
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.committee_routing import (  # noqa: E402
    CALIBRATION_PATH,
    CHAMBERS,
    Reference,
    data_hash,
    decide,
    examples,
    shares,
)

K_GRID = (5, 10, 15, 25)
ALPHA_GRID = (0.25, 0.5, 0.75, 1.0)
TEMPERATURE_GRID = (5.0, 10.0, 20.0)
THRESHOLD_GRID = np.round(np.arange(0.10, 0.801, 0.025), 3)
CUT_QUANTILE = 0.65
GAP_DAYS = 7
EXCLUDE_DAYS = 3
BOOTSTRAP = 4000


def outcomes(ref: Reference, routed: dict[str, np.ndarray], rows: np.ndarray) -> np.ndarray:
    """(issues, chambers, 3): [named, named correctly, has a labelled committee]."""
    exs = examples()
    out = np.zeros((len(rows), len(CHAMBERS), 3), int)
    for r, i in enumerate(rows):
        for c, ch in enumerate(CHAMBERS):
            col = routed[ch][r]
            gold = set(exs[i][ch])
            out[r, c] = [col >= 0, col >= 0 and ref.codes[ch][col] in gold, bool(gold)]
    return out


def prf(o: np.ndarray, beta: float = 0.5) -> tuple[float, float, float]:
    named, correct, gold = (o[..., j].sum() for j in range(3))
    p = correct / max(named, 1)
    r = correct / max(gold, 1)
    b2 = beta * beta
    return p, r, (1 + b2) * p * r / max(b2 * p + r, 1e-12)


def interval(o: np.ndarray) -> dict:
    rng = np.random.default_rng(0)
    draws = np.array([prf(o[rng.integers(0, len(o), len(o))])[:2] for _ in range(BOOTSTRAP)])
    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)
    p, r, f = prf(o)
    return {
        "issues": int(len(o)),
        "decisions": int(o.shape[0] * o.shape[1]),
        "named": int(o[..., 0].sum()),
        "with_a_labelled_committee": int(o[..., 2].sum()),
        "precision": [round(p, 3), round(lo[0], 3), round(hi[0], 3)],
        "recall": [round(r, 3), round(lo[1], 3), round(hi[1], 3)],
        "f0_5": round(f, 3),
    }


def main() -> None:
    ref = Reference()
    exs = examples()
    dates = np.array([datetime.date.fromisoformat(e["date"]) for e in exs])
    ordered = np.sort(dates)
    cut = ordered[int(CUT_QUANTILE * len(ordered))]
    train = dates < cut
    test = dates >= cut + datetime.timedelta(days=GAP_DAYS)
    train_rows, test_rows = np.flatnonzero(train), np.flatnonzero(test)
    far = np.abs((dates[:, None] - dates[None, :]).astype("timedelta64[D]").astype(int)) > EXCLUDE_DAYS
    train_mask = far[train_rows] & train[None, :]
    test_mask = np.repeat(train[None, :], len(test_rows), axis=0)
    x = ref.examples

    def run(rows, mask, k, alpha, temperature):
        return shares(x[rows], ref, k, alpha, temperature, mask)

    scores = {}
    for k, alpha, temperature in itertools.product(K_GRID, ALPHA_GRID, TEMPERATURE_GRID):
        combined = run(train_rows, train_mask, k, alpha, temperature)
        for t in THRESHOLD_GRID:
            routed = {ch: decide(combined[ch], float(t)) for ch in CHAMBERS}
            scores[(k, alpha, temperature, float(t))] = prf(outcomes(ref, routed, train_rows))

    def held_out(setting):
        k, alpha, temperature, t = setting
        combined = run(test_rows, test_mask, k, alpha, temperature)
        return outcomes(ref, {ch: decide(combined[ch], t) for ch in CHAMBERS}, test_rows)

    best = max(scores, key=lambda s: scores[s][2])
    proto_best = max((s for s in scores if s[1] == 1.0), key=lambda s: scores[s][2])
    k, alpha, temperature, threshold = best
    p, r, f = scores[best]
    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_committee_routing.py from "
            "app/data/committee_routing_examples.json and app/data/committee_jurisdictions.json "
            "(all-MiniLM-L6-v2): k, alpha, temperature and threshold for committee routing, "
            f"chosen by F0.5 on the issues dated before {cut} (each scored against the other "
            f"training issues more than {EXCLUDE_DAYS} days away). held_out scores the issues dated "
            f"{cut + datetime.timedelta(days=GAP_DAYS)} or later against all training issues; "
            "precision and recall are [value, 2.5%, 97.5%] bootstrap intervals over issues. "
            "prototypes_only is the best setting with alpha = 1 (no labelled votes), for comparison."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "data_hash": data_hash(),
        "examples": len(exs),
        "k": k,
        "alpha": alpha,
        "temperature": temperature,
        "threshold": threshold,
        "training": {"issues": int(train.sum()), "precision": round(p, 3), "recall": round(r, 3), "f0_5": round(f, 3)},
        "held_out": interval(held_out(best)),
        "prototypes_only": {
            "k": proto_best[0], "temperature": proto_best[2], "threshold": proto_best[3],
            "held_out": interval(held_out(proto_best)),
        },
    }
    CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
