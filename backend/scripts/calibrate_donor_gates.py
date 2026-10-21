"""Fit the donor classifier's embedding gates on labels the FEC's own
records supply, and write them to app/data/donor_gate_calibration.json
(AGENTS.md §3a).

Four gates (pipeline/analyze/donor_classifier_ai.py), each a cosine to a
natural-language prototype, each labelled by fields of the same Schedule A
receipts the pipeline reads (the cached FEC responses of a production
snapshot), never by hand:

- payment_processor (is_skip_entity's embedding tier): a contributor name
  that appears as a receipt's conduit_committee_name and whose every own
  receipt is a memo entry (a pass-through, never its own money) is a
  processor; a committee or organization that is never a conduit and has
  no memo receipt is not. Names in between are left out, as are names the
  keyword tier already decides.
- donor_type (classify_donor_type_semantic's threshold): measured on the
  names the semantic tier meets (entity type COM or none), each labelled
  by the one other type the FEC gives the same name elsewhere, mapped as
  tier 1 maps it (classify_donor_type_from_fec); the gate passes when its
  best non-skip prototype reaches the threshold, and is right when that is
  the type.
- skip_type (classify_donor_type_semantic's skip_threshold): on the same
  names, one the FEC types elsewhere as an individual (IND) is the noise the
  SKIP prototype describes; one it types as a committee is not.
- employer_skip (classify_employer_skips_batch): an employer-field value a
  committee's donors wrote as their occupation at least as often, by
  dollars, as their employer (normalize_finance's own test, the FEC's two
  fields testifying) is not an organization; a value never written as an
  occupation in any committee's totals is.
- transfer_memo (classify_transfer_memos_batch): a memo on a receipt the
  FEC codes as a transfer, earmark or joint-fundraising memo is a transfer;
  a memo on an ordinary contribution is not. A memo seen under both is
  left out.

Each threshold follows the Action Center's precision rule
(app/precision_rule.py): the grid point whose precision's exact lower 95%
bound is highest, used only when that bound is at least 0.80; otherwise
null and the gate's embedding tier never fires. Fitted on half the labels
(split by a hash of the text), measured on the other half.

Usage (loads the classification model; no network once it is cached):
    DATABASE_URL=sqlite:////path/to/snapshot.db python backend/scripts/calibrate_donor_gates.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.pipeline.analyze import donor_classifier_ai as dc  # noqa: E402
from app.pipeline.transform.normalize_finance import MISSING_VALUE_TEXT  # noqa: E402
from app.pipeline.vector_store import encode_normalized, get_embedding_model  # noqa: E402
from app.precision_rule import PRECISION_LOWER_BOUND, choose, summary  # noqa: E402
from app.time_utils import utcnow  # noqa: E402

GRID = [round(float(t), 2) for t in np.arange(0.30, 0.961, 0.01)]
# Gates measured but not applied, and why: each needs a decision the
# measurement alone can't make (2026-10-21).
NOT_APPLIED = {
    "donor_type": "Closing the tier hands its names to the kNN tier and normalize_finance's Org/Employees fallback, "
                  "whose accuracy on these names is unmeasured (the learning store the kNN reads is cleared with "
                  "every fingerprint change); 90% of the names it meets are PACs, so the fallback may be worse.",
    "skip_type": "A closed skip keeps individuals' names (7.7% of the names the tier meets) as donors under a "
                 "fallback type; whether that is better than a 61%-precise skip is a judgement on which error a "
                 "page should show.",
    "employer_skip": "The labels miss a class: status values (SELF-EMPLOYED, NONE, INFORMATION REQUESTED) are a filing "
                     "convention neither field testifies to, so 'never written as an occupation' counts them as "
                     "organizations, and the itemized-receipt paths have no two-field test to fall back on.",
    "transfer_memo": "The fit trades recall (29.5% to 8.7%) for precision (76% to 92%): a missed transfer counts money "
                     "moved between committees as a donation, a false one drops a real donation, and which costs a "
                     "page more is a decision, not a measurement.",
}
# The FEC's receipt-type codes for money another committee passed through
# or moved: transfers from affiliated and other committees and joint
# fundraisers (dc.AFFILIATED_RECEIPT_TYPES), earmarked contributions (15E)
# and joint-fundraising memos (15J).
TRANSFER_TYPES = dc.AFFILIATED_RECEIPT_TYPES | {"15E", "15J"}
# Ordinary contributions: from individuals (15), from committees and
# parties (11, 16), from candidates (15C).
ORDINARY_TYPES = {"11", "15", "15C", "16C"}
COMMITTEE_KINDS = {"PAC", "ORG", "PTY", "CCM", "CAN", "COM"}


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def _held(s: str) -> bool:
    return _h("half:" + s) % 2 == 1


def load(db) -> dict:
    receipts, employers, occupations = [], defaultdict(Counter), defaultdict(Counter)
    for key, data in db.execute(text("SELECT cache_key, data_json FROM api_cache WHERE tier = 'fec'")):
        if key.startswith("committee-receipts"):
            receipts += json.loads(data) or []
        elif key.startswith("by_employer-v1"):
            for r in json.loads(data) or []:
                employers[key.split("-", 2)[2]][(r.get("employer") or "").upper().strip()] += r.get("total") or 0
        elif key.startswith("by_occupation-v1"):
            for r in json.loads(data) or []:
                occupations[key.split("-", 2)[2]][(r.get("occupation") or "").upper().strip()] += r.get("total") or 0
    return {"receipts": receipts, "employers": employers, "occupations": occupations}


def labels(data: dict) -> dict[str, list[tuple[str, object]]]:
    """Each gate's labelled texts, at the mix of the population the gate
    actually meets: precision depends on how common the positives are, and
    a sample balanced by label overstated it (the first version of this
    script put the individual-noise gate at 86% precise; on the names the
    semantic tier sees it is far lower)."""
    receipts = data["receipts"]
    conduit = {(r.get("conduit_committee_name") or "").upper().strip() for r in receipts} - {""}
    own, memo = Counter(), Counter()
    kinds: dict[str, Counter] = defaultdict(Counter)
    memos = defaultdict(set)
    for r in receipts:
        name = (r.get("contributor_name") or "").upper().strip()
        if name:
            own[name] += 1
            memo[name] += r.get("memo_code") == "X"
            kinds[name][r.get("entity_type")] += 1
        m = (r.get("memo_text") or "").upper().strip()
        if len(m) >= 3:
            rt = r.get("receipt_type") or ""
            memos[m].add("transfer" if rt in TRANSFER_TYPES else "ordinary" if rt in ORDINARY_TYPES else "other")
    keyword = lambda n: any(k in n for k in dc._KNOWN_PAYMENT_PROCESSOR_KEYWORDS)  # noqa: E731
    committee = lambda n: bool(set(kinds[n]) & COMMITTEE_KINDS)  # noqa: E731
    processors = [n for n in conduit if own[n] and memo[n] == own[n] and not keyword(n)]
    not_processors = [n for n in kinds if committee(n) and n not in conduit and memo[n] == 0 and not keyword(n)]
    # The semantic tier meets the names tier 1 can't type: entity type COM
    # or none. Among those, a name the FEC types elsewhere (one type, not
    # COM) is labelled by it: an individual's (IND) is the noise the SKIP
    # prototype describes, a committee's names its donor type.
    seen_by_tier = {n: set(k) - {"COM", None} for n, k in kinds.items() if {"COM", None} & set(k) and len(n) >= 3}
    typed_elsewhere = {n: next(iter(other)) for n, other in seen_by_tier.items() if len(other) == 1}
    skip_labels = [(n, k == "IND") for n, k in typed_elsewhere.items()]
    type_labels = [(n, dc.FEC_ENTITY_TYPE_MAP[k]) for n, k in typed_elsewhere.items()
                   if k != "IND" and k in dc.FEC_ENTITY_TYPE_MAP]
    not_orgs, orgs = set(), set()
    ever_occupation = set().union(*[set(o) for o in data["occupations"].values()]) if data["occupations"] else set()
    for cc, emp in data["employers"].items():
        occ = data["occupations"].get(cc, Counter())
        for name, dollars in emp.items():
            if len(name) <= 3:
                continue  # the short-value rule decides these
            if name in MISSING_VALUE_TEXT or occ.get(name, 0) >= dollars > 0:
                not_orgs.add(name)
            elif name not in ever_occupation:
                orgs.add(name)
    orgs -= not_orgs
    transfer = [m for m, k in memos.items() if k == {"transfer"}]
    ordinary = [m for m, k in memos.items() if k == {"ordinary"}]
    return {
        "payment_processor": [(n, True) for n in sorted(processors)] + [(n, False) for n in sorted(not_processors)],
        "donor_type": sorted(type_labels),
        "skip_type": sorted(skip_labels),
        "employer_skip": [(n, True) for n in sorted(not_orgs)] + [(n, False) for n in sorted(orgs)],
        "transfer_memo": [(m, True) for m in sorted(transfer)] + [(m, False) for m in sorted(ordinary)],
    }


def fit_binary(texts: list[str], truth: np.ndarray, scores: np.ndarray) -> dict:
    """The precision-rule threshold for "score >= t means positive", fitted
    on one half and measured on the other."""
    held = np.array([_held(t) for t in texts])
    threshold, best = choose(scores[~held], truth[~held], GRID)
    at = threshold if threshold is not None else best
    return {
        "threshold": threshold, "best_bound_threshold": best,
        "labels": {"positive": int(truth.sum()), "negative": int((~truth).sum())},
        "fit_half": summary(scores[~held] >= at, truth[~held]),
        "held_out": summary(scores[held] >= at, truth[held]) if threshold is not None else
        {**summary(np.zeros(held.sum(), bool), truth[held]), "note": "gate closed: nothing passes"},
        "held_out_recall": round(float(((scores[held] >= at) & truth[held]).sum() / max(truth[held].sum(), 1)), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        sets = labels(load(db))
    model = get_embedding_model()
    proto = lambda key, txt: dc._get_skip_prototype_embedding(key, txt)  # noqa: E731
    out_gates = {}
    for gate, key, prototype in (
        ("payment_processor", "payment", dc._PAYMENT_PROCESSOR_PROTOTYPE),
        ("employer_skip", "employer_skip", dc._EMPLOYER_SKIP_PROTOTYPE),
        ("transfer_memo", "fund_transfer", dc._FUND_TRANSFER_PROTOTYPE),
    ):
        texts = [t for t, _ in sets[gate]]
        truth = np.array([bool(y) for _, y in sets[gate]])
        scores = encode_normalized(model, texts) @ proto(key, prototype) if texts else np.zeros(0)
        out_gates[gate] = fit_binary(texts, truth, scores) if texts else {"threshold": None, "labels": {}}
    # The semantic type tier: each prototype's cosine, as the classifier
    # computes it (PAC-suffix names are decided by its tier-0 rule).
    type_embs = dc._get_semantic_type_embeddings()
    kinds = [k for k in type_embs if k != "SKIP"]
    typed = [(n, t) for n, t in sets["donor_type"] if not dc.re.search(r"\bPAC\b", n)]
    emb = encode_normalized(model, [n for n, _ in typed])
    sims = np.stack([emb @ type_embs[k] for k in kinds], axis=1)
    best = sims.argmax(axis=1)
    right = np.array([kinds[b] == t for b, (_, t) in zip(best, typed)])
    best_score = sims.max(axis=1)
    # A pass is a classification; it is right when it names the FEC type.
    texts = [n for n, _ in typed]
    held = np.array([_held(t) for t in texts])
    threshold, best_t = choose(best_score[~held], right[~held], GRID)
    at = threshold if threshold is not None else best_t
    out_gates["donor_type"] = {
        "threshold": threshold, "best_bound_threshold": best_t,
        "labels": dict(Counter(t for _, t in typed)),
        "top1_right_all": round(float(right.mean()), 3),
        "fit_half": summary(best_score[~held] >= at, right[~held]),
        "held_out": summary(best_score[held] >= at, right[held]) if threshold is not None else
        {"note": "gate closed: the tier abstains"},
    }
    skip_texts = [t for t, _ in sets["skip_type"]]
    skip_truth = np.array([bool(y) for _, y in sets["skip_type"]])
    skip_scores = encode_normalized(model, skip_texts) @ type_embs["SKIP"]
    out_gates["skip_type"] = fit_binary(skip_texts, skip_truth, skip_scores)
    # Applied only where the evidence is one-sided (donor_classifier_ai's
    # comment at GATE_CALIBRATION_PATH); the rest are measured and recorded.
    for gate, why in NOT_APPLIED.items():
        out_gates[gate]["applied"] = False
        out_gates[gate]["not_applied_because"] = why
    out_gates["payment_processor"]["applied"] = True
    hashes = {g: hashlib.sha256(json.dumps(sorted(map(list, s)), default=str).encode()).hexdigest()
              for g, s in sets.items()}
    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_donor_gates.py from the cached FEC Schedule A receipts and "
            "employer/occupation totals of a production snapshot: labels from the FEC's own fields (conduit and memo "
            "codes, entity types, receipt types, the employer and occupation fields), never by hand (the script's "
            "docstring gives each). Each threshold: the grid point whose precision's exact lower 95% bound is highest "
            f"on half the labels (split by a hash of the text), used only when that bound is at least "
            f"{PRECISION_LOWER_BOUND}, else null (the embedding tier never fires); held_out scores the other half."
        ),
        "_as_of": utcnow().date().isoformat(),
        "gates": out_gates,
        "labels_sha256": hashes,
    }
    print(json.dumps(out, indent=1))
    if not args.dry_run:
        dc.GATE_CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
