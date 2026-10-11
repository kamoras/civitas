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
story runs for days under different titles): k, alpha and temperature by
F0.5, then the threshold by the Action Center's precision rule — the lowest
whose exact lower 95% bound on precision is at least PRECISION_LOWER_BOUND,
or, when none is, the one with the highest bound. Decisions pass the
agreement gate (committee_routing.decide). The held-out issues are then
scored against every training issue with that setting, the newest
RECENT_ISSUES of them reported on their own, and every threshold's held-out
precision and recall are written as a curve to read, never to choose from:
a threshold picked on the held-out issues would make them a second training
set. alpha is never 0: the prototypes always take part; "prototypes_only"
reports alpha = 1 alone, without the gate.

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
from scipy.stats import beta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.committee_routing import (  # noqa: E402
    CALIBRATION_PATH,
    CHAMBERS,
    Reference,
    Shares,
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
# The Action Center's bar for naming a committee as a public call to action:
# the precision's lower 95% bound, not its point estimate (about 0.9 at
# this sample size). A fixed policy, not a fitted value.
PRECISION_LOWER_BOUND = 0.80
# The newest held-out issues, reported on their own as a fresh-time check.
RECENT_ISSUES = 30


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


def exact_lower(o: np.ndarray) -> float:
    """Clopper-Pearson lower 95% bound on precision (correct of named). The
    bootstrap's percentile interval collapses to [1, 1] when every one of a
    handful of named committees is right, which would let four lucky
    decisions pass a precision rule; the exact binomial bound does not."""
    named, correct = int(o[..., 0].sum()), int(o[..., 1].sum())
    return float(beta.ppf(0.025, correct, named - correct + 1)) if correct else 0.0


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
        "precision_exact_lower": round(exact_lower(o), 3),
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

    def routed_outcomes(rows, mask, setting, gated=True):
        k, alpha, temperature, t = setting
        combined = run(rows, mask, k, alpha, temperature)
        if not gated:
            combined = {ch: Shares(s.combined, np.ones_like(s.agree)) for ch, s in combined.items()}
        return outcomes(ref, {ch: decide(combined[ch], t) for ch in CHAMBERS}, rows)

    # k, alpha and temperature by F0.5 on the training issues; then the
    # threshold by the precision rule, on the training issues too.
    k, alpha, temperature, _ = max(scores, key=lambda s: scores[s][2])
    train_curve = {float(t): interval(routed_outcomes(train_rows, train_mask, (k, alpha, temperature, float(t))))
                   for t in THRESHOLD_GRID}
    meeting = [t for t, iv in train_curve.items() if iv["precision_exact_lower"] >= PRECISION_LOWER_BOUND]
    threshold = min(meeting) if meeting else max(
        train_curve, key=lambda t: (train_curve[t]["precision_exact_lower"], -t))
    setting = (k, alpha, temperature, threshold)

    held = routed_outcomes(test_rows, test_mask, setting)
    recent = np.argsort(dates[test_rows], kind="stable")[-RECENT_ISSUES:]
    held_out_curve = []
    for t in THRESHOLD_GRID:
        o = routed_outcomes(test_rows, test_mask, (k, alpha, temperature, float(t)))
        iv = interval(o)
        iv["threshold"] = float(t)
        iv["issues_with_a_committee"] = round(float(o[..., 0].any(axis=1).mean()), 3)
        held_out_curve.append(iv)
    proto_best = max((s for s in scores if s[1] == 1.0), key=lambda s: scores[s][2])
    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_committee_routing.py from "
            "app/data/committee_routing_examples.json and app/data/committee_jurisdictions.json "
            "(all-MiniLM-L6-v2). A committee is named only when the prototype and labelled-issue "
            "signals pick the same one and the combined share clears the threshold "
            f"(committee_routing.decide). k, alpha and temperature by F0.5, and the threshold as the "
            f"lowest whose precision's exact (Clopper-Pearson) lower 95% bound is at least "
            f"{PRECISION_LOWER_BOUND} (else the "
            f"one with the highest lower bound), both on the issues dated before {cut}, each scored "
            f"against the other training issues more than {EXCLUDE_DAYS} days away. held_out scores the "
            f"issues dated {cut + datetime.timedelta(days=GAP_DAYS)} or later against all training issues; "
            f"held_out_recent is the {RECENT_ISSUES} newest of them; held_out_curve is every threshold "
            "on the held-out issues, for reading only (nothing is chosen from it). Precision and recall "
            "are [value, 2.5%, 97.5%] bootstrap intervals over issues. prototypes_only is the best "
            "setting with alpha = 1 and no agreement gate, for comparison."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "data_hash": data_hash(),
        "examples": len(exs),
        "k": k,
        "alpha": alpha,
        "temperature": temperature,
        "threshold": threshold,
        "precision_rule_met_on_training": bool(meeting),
        "training": train_curve[threshold] | {"issues": int(train.sum())},
        "held_out": interval(held),
        "held_out_recent": interval(held[recent]) | {
            "from": str(dates[test_rows][recent].min()), "to": str(dates[test_rows][recent].max())},
        "held_out_curve": held_out_curve,
        "prototypes_only": {
            "k": proto_best[0], "temperature": proto_best[2], "threshold": proto_best[3],
            "held_out": interval(routed_outcomes(test_rows, test_mask, proto_best, gated=False)),
        },
    }
    CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k_: v for k_, v in out.items() if k_ != "held_out_curve"}, indent=1))


if __name__ == "__main__":
    main()
