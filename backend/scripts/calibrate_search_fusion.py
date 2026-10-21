"""Calibrate the /search results page's one list (app/site_search.fuse).

Writes app/data/search_fusion_calibration.json: the reciprocal-rank fusion
of site search's matches with Explore's hybrid document ranking (`k`,
`document_weight`, and the gate `strong_match` / `weak_weight`), and what
the page scored before and after on queries the fit never saw.

Known items from both sides, each built from its target's own name or words
(AGENTS.md §3b): site search's probes (scripts/calibrate_site_search.py:
names, surnames, acronyms, identifiers, typos, pages, section words and
document titles; not `prefix`, the autocomplete's) and Explore's document
probes (scripts/evaluate_explore_search.py: title, paraphrase, identifier,
rare term, the document's own citation and number). Each is run once
through the page's read path: `site_search.answer` (a direct jump, else the
ranked matches) and, when nothing is jumped to, `hybrid_search` for the
documents. Only the fusion is redone per candidate.

Fit and report on different targets: each probe's half comes from a hash
of its target. On the fit half, the plain fusion (no gate) and the gated
one are each chosen by grid search on the mean over probe types of MRR at
10 (macro, so a type with many probes can't buy the rest), under one
constraint: no site-search type's MRR below the page's before by more than
one standard error. The gate is kept only if it beats the plain fusion by
more than one standard error of the paired difference (the one-standard-
error rule, Hastie, Tibshirani & Friedman 2009). The held-out half is
measured once, at the end.

"Before" is the page as it was: the ranked matches alone, documents only by
title. A direct jump counts as rank 1 when it lands on an accepted page, and
as a miss otherwise.

Run in the pipeline container, against the live indexes:

    python scripts/calibrate_search_fusion.py [--samples 150] [--per-type 150]
        [--index /data/site_search.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import itertools
import json
import math
import pathlib
import random
import statistics
import sys
import time
from dataclasses import asdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sqlalchemy import text  # noqa: E402

import calibrate_site_search as site_probes  # noqa: E402
import evaluate_explore_search as doc_probes  # noqa: E402
from app.site_search import FUSION_PATH, Fusion, answer, document_url, fused_order  # noqa: E402
from app.site_search_index import SITE_SEARCH_DB  # noqa: E402

# Wide enough to see the plateau's extent: on the first runs (2026-10-20)
# held-out MRR was the same from the grid's lowest corner up, and ties then
# went to the first point tried (the corner); they now go to the plainest.
GRID = {
    "k": [0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 60.0],
    "document_weight": [0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0],
}
GATE = {
    "strong_match": [0.5, 0.6, 0.7, 0.8, 0.9],
    "weak_weight": [0.0, 0.25, 0.5],
}
CUTOFF = 10
# As many documents as the page fuses (api/search.DOCUMENTS).
DOCUMENTS = 30


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def build(index: str, db, samples: int, per_type: int, seed: int) -> list[dict]:
    """Every probe as {type, side, q, accept, half}."""
    out = []
    by_type: dict[str, list[dict]] = collections.defaultdict(list)
    for p in site_probes.probes(index, db):
        if p["type"] != "prefix":
            by_type[p["type"]].append(p)
    for t, group in by_type.items():
        for p in sorted(group, key=lambda p: _h(p["q"] + p["url"]))[:per_type]:
            out.append({"type": t, "side": "site", "q": p["q"], "accept": set(p["accept"]) | {p["url"]},
                        "half": p["half"]})

    rows = db.execute(text(
        "SELECT id, title, body, external_id, doc_type, identifiers, comment_url FROM explore_documents ORDER BY id"
    )).fetchall()
    random.seed(seed)
    sample = random.sample(rows, min(samples, len(rows)))
    df: dict[str, int] = collections.Counter()
    for r in sample:
        for term in set(doc_probes._terms(f"{r.title} {(r.body or '')[:6000]}")):
            df[term] += 1
    docs = [dict(r._mapping) for r in sample]
    for p in doc_probes.build_probes(docs, df, len(sample)) + doc_probes.build_own_identifier_probes(docs):
        url = document_url(p["doc_id"])
        out.append({"type": f"doc:{p['style']}", "side": "documents", "q": p["query"], "accept": {url},
                    "half": _h(url) % 2})
    return out


def gather(probes: list[dict], index: str, db) -> list[dict]:
    """Each probe's direct jump, ranked matches and hybrid documents: the
    page's whole read path, once."""
    from app.services.explore_search import hybrid_search

    t0 = time.time()
    for i, p in enumerate(probes):
        a = answer(p["q"], index, db)
        p["direct"] = a.direct.url if a.direct else None
        p["ranked"] = a.ranked
        p["docs"] = [] if a.direct else hybrid_search(db, p["q"], limit=DOCUMENTS)["results"]
        if i % 200 == 0:
            print(f"  gathered {i}/{len(probes)} ({time.time() - t0:.0f}s)", file=sys.stderr)
    return probes


def _rr(order: list[str], accept: set[str]) -> float:
    for i, u in enumerate(order[:CUTOFF]):
        if u in accept or u.split("#")[0] in accept:
            return 1.0 / (i + 1)
    return 0.0


def reciprocal_ranks(p: dict, f: Fusion | None) -> float:
    """The probe's reciprocal rank on the page: before (`f` None) or fused."""
    if p["direct"]:
        return 1.0 if p["direct"] in p["accept"] else 0.0
    if f is None:
        return _rr([h.url for h in p["ranked"]], p["accept"])
    return _rr(fused_order(p["ranked"], p["docs"], f)[0], p["accept"])


def per_type(probes: list[dict], f: Fusion | None) -> dict[str, list[float]]:
    out: dict[str, list[float]] = collections.defaultdict(list)
    for p in probes:
        out[p["type"]].append(reciprocal_ranks(p, f))
    return out


def _se(xs: list[float]) -> float:
    return statistics.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else 0.0


def macro(rr: dict[str, list[float]]) -> float:
    return statistics.fmean(statistics.fmean(v) for v in rr.values())


def _plainness(f: Fusion) -> tuple[float, float, float]:
    """How far a fusion is from plain RRF (k=60, equal weights, no gate):
    MRR at 10 is flat over a wide plateau of these, so ties go to the
    plainest."""
    return (abs(math.log(f.document_weight)), abs(math.log(f.k / 60.0)), f.strong_match)


def fit(probes: list[dict], candidates: list[Fusion]) -> Fusion | None:
    candidates = sorted(candidates, key=_plainness)
    before = per_type(probes, None)
    floor = {t: statistics.fmean(v) - _se(v) for t, v in before.items() if not t.startswith("doc:")}
    best, choice = -1.0, None
    for f in candidates:
        rr = per_type(probes, f)
        if any(statistics.fmean(rr[t]) < floor[t] for t in floor):
            continue
        m = macro(rr)
        if m > best + 1e-9:
            best, choice = m, f
    return choice


def _paired_gain(probes: list[dict], a: Fusion, b: Fusion) -> tuple[float, float]:
    """b over a: the macro MRR difference and its standard error, from each
    type's paired per-probe differences."""
    diffs: dict[str, list[float]] = collections.defaultdict(list)
    for p in probes:
        diffs[p["type"]].append(reciprocal_ranks(p, b) - reciprocal_ranks(p, a))
    types = list(diffs)
    gain = statistics.fmean(statistics.fmean(diffs[t]) for t in types)
    se = math.sqrt(sum(_se(diffs[t]) ** 2 for t in types)) / len(types)
    return gain, se


def report(probes: list[dict], f: Fusion) -> dict:
    before, after = per_type(probes, None), per_type(probes, f)
    out = {}
    for t in sorted(before):
        b, a = before[t], after[t]
        out[t] = {"probes": len(b),
                  "mrr_before": round(statistics.fmean(b), 3), "mrr_after": round(statistics.fmean(a), 3),
                  "r1_before": round(sum(x == 1.0 for x in b) / len(b), 3),
                  "r1_after": round(sum(x == 1.0 for x in a) / len(a), 3),
                  f"r{CUTOFF}_before": round(sum(x > 0 for x in b) / len(b), 3),
                  f"r{CUTOFF}_after": round(sum(x > 0 for x in a) / len(a), 3)}
    for side, label in (("site", "site_search_types"), ("documents", "document_types")):
        types = [t for t in before if (t.startswith("doc:")) == (side == "documents")]
        out[label] = {"mrr_before": round(statistics.fmean(statistics.fmean(before[t]) for t in types), 3),
                      "mrr_after": round(statistics.fmean(statistics.fmean(after[t]) for t in types), 3)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=SITE_SEARCH_DB)
    ap.add_argument("--samples", type=int, default=150, help="documents sampled for document probes")
    ap.add_argument("--per-type", type=int, default=150, help="site-search probes kept per type")
    ap.add_argument("--seed", type=int, default=20260730)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        probes = gather(build(args.index, db, args.samples, args.per_type, args.seed), args.index, db)
    finally:
        db.close()
    fit_half = [p for p in probes if p["half"] == 0]
    held = [p for p in probes if p["half"] == 1]

    plain = fit(fit_half, [Fusion(k=k, document_weight=w) for k, w in itertools.product(*GRID.values())])
    if plain is None:
        print("No fusion keeps every site-search type within a standard error of before.")
        raise SystemExit(1)
    gated = fit(fit_half, [Fusion(k=k, document_weight=w, strong_match=s, weak_weight=g)
                           for k, w, s, g in itertools.product(*GRID.values(), *GATE.values())])
    chosen, gate_note = plain, "no gated fusion met the constraint"
    if gated is not None:
        gain, se = _paired_gain(fit_half, plain, gated)
        keep = gain > se
        chosen = gated if keep else plain
        gate_note = (f"gate {'kept' if keep else 'dropped'}: macro MRR gain {gain:.4f} on the fit half, "
                     f"standard error {se:.4f}")
    out = {
        "_source": (
            "scripts/calibrate_search_fusion.py on the live indexes "
            f"({datetime.date.today().isoformat()}): fitted on half the targets' known-item probes "
            "(site search's and Explore's), measured on the other half."
        ),
        "params": asdict(chosen),
        "structure": gate_note,
        "cutoff": CUTOFF,
        "held_out": report(held, chosen),
    }
    print(json.dumps(out, indent=2))
    if not args.dry_run:
        FUSION_PATH.write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
