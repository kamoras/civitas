"""Calibrate the Action Center's committee routing on labels the data supplies.

Writes the calibration app/committee_routing.py reads (the kNN size k, the
prototype weight alpha, the softmax temperature and the threshold, with the
held-out measurement), and, on the data volume, the reference it was fitted
with (committee_routing_reference.json: the labelled texts) and their
embeddings (committee_routing_embeddings.npz, a cache: only new texts are
embedded). Run daily by app/data_refresh.py, which installs the calibration
only under the acceptance rule.

The labels (committee_routing.labelled_texts): each bill the pipeline has
stored the actions of, labelled with the committees Congress.gov records it
was referred to, and "none" texts (feed articles their publisher filed under
another desk naming no U.S. official or agency; election coverage matched to
a race). Nothing is labelled by hand.

What is measured. Each (text, chamber) where the text has a label is one
decision: name a committee or abstain. A bill is right when the committee
named is one it was referred to in that chamber; a "none" text is never
right to name one. Precision = right / named.

Method. The texts are split into FOLDS blocks of consecutive days, the
bills and the "none" texts each by their own dates (they span different
stretches: the bills the whole Congress, the none texts the feeds' recent
months, and one block of all days would hold out the none texts with no
none text left to vote). Each text is scored against the texts outside its
block. For every setting (k, alpha, temperature) the threshold is chosen on
the other blocks by the precision rule (precision_rule.choose,
LOWEST_PASSING: with thousands of decisions the exact bound sits within
about 0.015 of the precision, so the lowest passing threshold is not the
noisy edge BEST_BOUND guards a small labelled set against) and scores the
block left out; pooled, that is the setting's held-out record. The setting
kept is the one naming the most held-out committees whose pooled held-out
precision's exact lower bound meets the bar (else the one with the highest
bound), and its threshold is then chosen on all the texts the same way.
`precision_rule_met` is whether that held-out bound meets the bar: the
Action Center words the row as a fact only then. The agreement gate
(committee_routing.decide) always applies: measured 2026-10-23 on the
hand-labelled issues the old fit was held out on, each k without it named
committees at 0.78-0.83 precision (bounds 0.62-0.66), more of them for
stories no committee handles, against 0.91-0.97 with it.

With --in-use (the pipeline's refresh), the fit in use is scored on the same
held-out decisions, and the comparison (precision_rule.compare) is written as
`_comparison`; data_refresh installs the refit only when precision_rule.accept
passes it, or when its parameters are the fit in use's (`unchanged`: the
reference has moved, the method has not).

`hand_labelled_check` scores the refit on the hand-labelled issues
(app/data/committee_routing_examples.json), each against every labelled text
except "none" texts within EXCLUDE_DAYS of it (one story runs for days), as
a held-out check, never a choice; `old_fit_window` is the stretch the
hand-labelled fit was held out on (its newest issues, CUT_QUANTILE and
GAP_DAYS as that fit used them), with the fit in use beside it.

Measured on a copy of production, 2026-10-23: 18,640 labelled texts
(15,933 bills, 2,707 "none"), 24,081 decisions. The refit (k 15, alpha 0,
threshold 0.5) held out at precision 0.817 (exact lower bound 0.803; 3,122
named; bills 0.864, and 169 of 5,414 "none" decisions named), recall 0.243;
the hand-labelled fit in use, on the same decisions, 0.632 (bound 0.612),
recall 0.152, so the acceptance rule adopts the refit. On the hand-labelled
issues the old fit was held out on (2026-08-24 on, 136 issues) the refit
named 38 committees, 36 right (0.947, bound 0.823, recall 0.176), the old
fit 44, 36 right (0.818, bound 0.673, recall 0.176); on all 460, 97 named,
85 right (0.876, bound 0.794, recall 0.138).

Usage (loads all-MiniLM-L6-v2; no network once the model is cached):
    python backend/scripts/calibrate_committee_routing.py --output PATH \\
        [--in-use PATH] [--database sqlite:////data/civitas.db]
"""

from __future__ import annotations

import argparse
import datetime
import itertools
import json
import os
import pathlib
import sys
import tempfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import committee_routing as cr  # noqa: E402
from app import precision_rule  # noqa: E402
from app.atomic_write import runtime_data_path  # noqa: E402

K_GRID = (5, 15, 41)
ALPHA_GRID = (0.0, 0.25, 0.5)
TEMPERATURE_GRID = (5.0, 10.0, 20.0)
THRESHOLD_GRID = [round(t, 3) for t in np.arange(0.20, 0.951, 0.025)]
VARIANT = precision_rule.LOWEST_PASSING
FOLDS = 4
EXCLUDE_DAYS = 3
CUT_QUANTILE = 0.65
GAP_DAYS = 7
# Texts scored at once: bounds the similarity block (CHUNK x texts) in memory.
CHUNK = 250
# A reference that shrinks to under this share of the one in use is a read
# that failed part-way, not a Congress that stopped referring bills: the
# script refuses it rather than fit on it.
MIN_REFERENCE_SHARE = 0.5


def stratified_folds(ref: cr.Reference) -> np.ndarray:
    """Each text's block: consecutive days, within its own kind."""
    fold = np.zeros(len(ref.days), int)
    for kind in set(ref.kinds.tolist()):
        rows = ref.kinds == kind
        fold[rows] = precision_rule.fold_of(ref.days[rows], FOLDS)
    return fold


def cross_fitted(ref: cr.Reference, fold: np.ndarray) -> cr.Neighbours:
    """Every text's neighbours among the texts outside its block."""
    k_max = max(K_GRID)
    parts = []
    for f in range(FOLDS):
        rows = np.flatnonzero(fold == f)
        outside = fold != f
        for start in range(0, len(rows), CHUNK):
            r = rows[start:start + CHUNK]
            parts.append((r, cr.neighbours(ref.examples[r], ref, k_max, np.repeat(outside[None, :], len(r), 0))))
    order = np.argsort(np.concatenate([r for r, _ in parts]))

    def stack(field):
        return {ch: np.concatenate([getattr(nb, field)[ch] for _, nb in parts])[order] for ch in cr.CHAMBERS}

    return cr.Neighbours(stack("proto"), stack("index"), stack("sims"))


def decisions(ref: cr.Reference, s: dict[str, cr.Shares], rows: np.ndarray | None = None):
    """(score, correct, row) for every labelled (text, chamber)."""
    sc, co, rw = [], [], []
    for ch in cr.CHAMBERS:
        score, best = cr.scores(s[ch])
        v = ref.votes[ch]
        labelled = np.flatnonzero(v.sum(axis=1) > 0)
        sc.append(score[labelled])
        co.append(v[labelled, best[labelled]] > 0)
        rw.append(labelled)
    return np.concatenate(sc), np.concatenate(co), np.concatenate(rw)


def held_out_passed(score: np.ndarray, correct: np.ndarray, block: np.ndarray, grid=THRESHOLD_GRID) -> np.ndarray:
    passed = np.zeros(len(score), bool)
    for f in range(FOLDS):
        test = block == f
        if not test.any() or test.all():
            continue
        t, _ = precision_rule.choose(score[~test], correct[~test], grid, VARIANT)
        if t is not None:
            passed[test] = score[test] >= t
    return passed


def recall(passed: np.ndarray, correct: np.ndarray) -> float:
    return round(float((passed & correct).sum()) / max(int(correct.sum()), 1), 3)


def issue_record(ref_codes, cal_routed: dict[str, np.ndarray], issues: list[cr.Labelled], rows) -> dict:
    named = right = gold = 0
    for ch in cr.CHAMBERS:
        for i in rows:
            labelled = set(issues[i].labels[ch])
            gold += bool(labelled)
            col = cal_routed[ch][i]
            if col >= 0:
                named += 1
                right += ref_codes[ch][col] in labelled
    return {"issues": len(rows), "named": named, "right": right,
            "precision": round(right / named, 3) if named else None,
            "precision_exact_lower": round(precision_rule.exact_lower(right, named), 3),
            "recall": round(right / max(gold, 1), 3)}


def write_atomic(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--in-use", default=None, help="the calibration in use, to measure the refit against")
    ap.add_argument("--database", default=None, help="SQLAlchemy URL of the main database (default: settings)")
    args = ap.parse_args()
    if args.database:
        db = Session(create_engine(args.database))
    else:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        texts = cr.labelled_texts(db)
    finally:
        db.close()
    reference_path = runtime_data_path(cr.REFERENCE_NAME)
    try:
        before = len(json.loads(pathlib.Path(reference_path).read_text()).get("texts") or [])
    except (OSError, ValueError):
        before = 0
    if len(texts) < MIN_REFERENCE_SHARE * before:
        raise SystemExit(f"labelled texts fell from {before} to {len(texts)}: a partial read, not fitted on")
    kinds = {k: sum(t.kind == k for t in texts) for k in ("bill", "none")}
    if not kinds["bill"] or not kinds["none"]:
        raise SystemExit(f"labelled texts hold {kinds}: both kinds are needed to fit")

    vectors = cr.cached_embeddings([t.text for t in texts], write=True)
    ref = cr.Reference(texts=texts, vectors=vectors)
    fold = stratified_folds(ref)
    nb = cross_fitted(ref, fold)

    settings = []
    for k, alpha in itertools.product(K_GRID, ALPHA_GRID):
        for temperature in (TEMPERATURE_GRID if alpha else TEMPERATURE_GRID[:1]):
            score, correct, rows = decisions(ref, cr.shares_from(nb, ref, k, alpha, temperature))
            passed = held_out_passed(score, correct, fold[rows])
            settings.append({"k": k, "alpha": alpha, "temperature": temperature,
                             **precision_rule.summary(passed, correct), "recall": recall(passed, correct),
                             "_passed": passed, "_score": score})
    meeting = [s for s in settings if s["precision_exact_lower"] >= precision_rule.PRECISION_LOWER_BOUND]
    best = (max(meeting, key=lambda s: (s["passed"], s["precision_exact_lower"])) if meeting
            else max(settings, key=lambda s: (s["precision_exact_lower"], s["passed"])))
    k, alpha, temperature = best["k"], best["alpha"], best["temperature"]
    score, correct, rows = decisions(ref, cr.shares_from(nb, ref, k, alpha, temperature))
    threshold, best_bound_t = precision_rule.choose(score, correct, THRESHOLD_GRID, VARIANT)
    passed = best["_passed"]
    met = bool(meeting) and threshold is not None
    by_kind = {kind: {**precision_rule.summary(passed[ref.kinds[rows] == kind], correct[ref.kinds[rows] == kind]),
                      "decisions": int((ref.kinds[rows] == kind).sum())} for kind in ("bill", "none")}

    # The fit in use on the same held-out decisions.
    in_use = json.loads(pathlib.Path(args.in_use).read_text()) if args.in_use else None
    hand = cr.hand_labelled()
    hand_ref = None
    if in_use is not None:
        if in_use.get("reference") == "data":
            s_use = cr.shares_from(nb, ref, int(in_use["k"]), float(in_use["alpha"]), float(in_use["temperature"]))
        else:
            hand_ref = cr.Reference(texts=hand)
            s_use = cr.shares(ref.examples, hand_ref, int(in_use["k"]), float(in_use["alpha"]),
                              float(in_use["temperature"]))
        score_use, _, _ = decisions(ref, s_use)
        comparison = precision_rule.compare(correct, passed, score_use >= float(in_use["threshold"]))
        unchanged = in_use.get("reference") == "data" and all(
            in_use.get(key) == value for key, value in
            (("k", k), ("alpha", alpha), ("temperature", temperature), ("threshold", threshold)))
        comparison["unchanged"] = unchanged
        if threshold is None:
            comparison["refused"] = ["no threshold meets the precision rule on the data-supplied labels"]
    else:
        comparison = None

    # The hand-labelled issues, held out: never chosen from.
    hand_vecs = cr.embed([t.text for t in hand])
    hand_days = np.array([t.day for t in hand])
    near = np.abs(hand_days[:, None] - ref.days[None, :]) <= EXCLUDE_DAYS
    mask = ~(near & (ref.kinds == "none")[None, :])
    t_used = threshold if threshold is not None else best_bound_t
    s_hand = cr.shares(hand_vecs, ref, k, alpha, temperature, mask)
    routed = {ch: cr.decide(s_hand[ch], t_used) for ch in cr.CHAMBERS}
    ordered = np.sort(hand_days)
    cut = ordered[int(CUT_QUANTILE * len(ordered))]
    window = np.flatnonzero(hand_days >= cut + GAP_DAYS)
    check = {"all": issue_record(ref.codes, routed, hand, range(len(hand))),
             "old_fit_window": issue_record(ref.codes, routed, hand, window) | {
                 "from": datetime.date.fromordinal(int(cut + GAP_DAYS)).isoformat()}}
    if in_use is not None and in_use.get("reference") != "data":
        hand_ref = hand_ref or cr.Reference(texts=hand)
        train = hand_days < cut
        s_old = cr.shares(hand_vecs[window], hand_ref, int(in_use["k"]), float(in_use["alpha"]),
                          float(in_use["temperature"]), np.repeat(train[None, :], len(window), 0))
        old_routed = {ch: np.full(len(hand), -1) for ch in cr.CHAMBERS}
        for ch in cr.CHAMBERS:
            old_routed[ch][window] = cr.decide(s_old[ch], float(in_use["threshold"]))
        check["old_fit_window_in_use"] = issue_record(hand_ref.codes, old_routed, hand, window)

    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_committee_routing.py from the labels the stored data "
            "supplies (committee_routing.labelled_texts: bills by their Congress.gov committee referrals, and "
            "feed articles filed under another desk naming no U.S. official or agency and election coverage "
            "matched to a race as no committee's), all-MiniLM-L6-v2. k, alpha and temperature are the setting "
            "naming the most held-out committees whose pooled held-out precision's exact lower 95% bound is at "
            f"least {precision_rule.PRECISION_LOWER_BOUND}; the threshold is the precision rule's lowest passing "
            f"one on all labelled decisions. held_out pools {FOLDS} blocks of consecutive days (bills and none "
            "texts each by their own dates), each scored against the texts outside it with the threshold the "
            "other blocks chose. hand_labelled_check scores the hand-labelled issues "
            "(app/data/committee_routing_examples.json) as a held-out check, never a choice."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "reference": "data",
        "labels": {"texts": len(texts), **kinds, "decisions": int(len(correct))},
        "k": k,
        "alpha": alpha,
        "temperature": temperature,
        "threshold": threshold if threshold is not None else best_bound_t,
        "precision_rule": VARIANT,
        "precision_rule_met": met,
        "held_out": {**precision_rule.summary(passed, correct), "recall": recall(passed, correct),
                     "by_kind": by_kind},
        "settings": [{key: v for key, v in s.items() if not key.startswith("_")} for s in settings],
        "hand_labelled_check": check,
    }
    if comparison is not None:
        out["_comparison"] = comparison
    # The reference beside the fit: written before the calibration, so a
    # calibration installed never names a reference that isn't there.
    write_atomic(reference_path, cr.reference_doc(texts))
    pathlib.Path(args.output).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({key: v for key, v in out.items() if key != "settings"}, indent=1))


if __name__ == "__main__":
    main()
