"""Which congressional committee handles an Action Center issue.

An issue with no member named in its coverage used to offer only "find your
members" (2026-10-10: 80% of the last 90 issues offered nothing specific to
the story). The committee with jurisdiction is a real, specific place to
take a concern, and its chair and ranking member are already stored
(committee_leadership). This module names that committee, one per chamber,
or none.

Two signals, both in all-MiniLM-L6-v2 space, combined per chamber:

  1. Jurisdiction prototypes (tier 2). Each committee's subjects as House
     Rule X and Senate Rule XXV list them (app/data/committee_jurisdictions.json,
     scripts/fetch_committee_jurisdictions.py). A committee's score is its
     best-matching subject. The rules' subjects are short and some are
     generic ("Research and development", "Government information"), and
     a generic subject sits close to every issue, so each subject's
     similarity is hubness-corrected (CSLS, Conneau et al. 2018: 2·cos
     minus the subject's mean similarity to its HUB_K nearest reference
     issues). The chamber's scores become shares by a softmax.
  2. Labelled issues (tier 3). The similarity-weighted committee votes of
     the k nearest hand-labelled issues (app/data/committee_routing_examples.json),
     the same kNN shape as the relevance gate's (action_center.relevance_votes).
     An example whose story no committee handles votes "none".

combined = alpha·prototype share + (1 − alpha)·kNN share. The top committee
is named only when both signals pick it, combined clears the threshold and
it beats the "none" vote; otherwise the chamber abstains (decide). A wrong committee misleads a reader in the
one place the page tells them where to go, so the fit favours precision.

Measured (scripts/calibrate_committee_routing.py, 2026-10-11): prototypes
alone are 0.38 precise held out — the rules' 1946 subject wording
("Interoceanic canals") is far from news prose. Blended without the
agreement gate, 0.77-0.81 at any threshold. With the gate, 0.82 (bootstrap
0.67-0.94) at the threshold the training issues chose, naming a committee
for 22% of held-out issues. The Action Center's bar (precision's exact
lower 95% bound at least 0.80) is met at no threshold: precision does not
rise with the threshold, and almost every miss is a story labelled as no
committee's, so the frontend words the row as a likely match. The full
held-out curve, the fitted alpha, k, temperature and threshold are in
app/data/committee_routing_calibration.json; rerun the script when examples
or jurisdictions change (test_committee_routing_calibration_matches_data
fails until you do).

Lives outside app/pipeline/, like live_results/, so it is outside the
analysis-code fingerprint without touching the hashed exemption lists:
nothing classified or scored reads it, and its only caller is the Action
Center (exempt, senate_pipeline._ACTION_CENTER_PATHS).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import NamedTuple

import numpy as np

CHAMBERS = ("house", "senate")
# CSLS's neighbourhood size, as in the paper (k = 10). Not fitted.
HUB_K = 10

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
JURISDICTIONS_PATH = _DATA_DIR / "committee_jurisdictions.json"
EXAMPLES_PATH = _DATA_DIR / "committee_routing_examples.json"
CALIBRATION_PATH = _DATA_DIR / "committee_routing_calibration.json"

_cache: dict = {}


def _load(path: pathlib.Path) -> dict:
    if path not in _cache:
        _cache[path] = json.loads(path.read_text())
    return _cache[path]


def jurisdictions() -> dict[str, dict]:
    return _load(JURISDICTIONS_PATH)["committees"]


def examples() -> list[dict]:
    return _load(EXAMPLES_PATH)["examples"]


def calibration() -> dict:
    return _load(CALIBRATION_PATH)


def data_hash() -> str:
    """Identifies the labelled set and prototypes a calibration was fitted to."""
    payload = json.dumps([
        [[e["text"], e["house"], e["senate"]] for e in examples()],
        {code: c["jurisdiction"] for code, c in sorted(jurisdictions().items())},
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def issue_text(title: str, summary: str) -> str:
    """What routing embeds — the examples' `text` is built the same way."""
    return f"{title}. {summary or ''}".strip()


def embed(texts: list[str]) -> np.ndarray:
    from app.pipeline.vector_store import get_similarity_model

    return get_similarity_model().encode(texts, show_progress_bar=False, normalize_embeddings=True)


class Reference:
    """Embeddings of the prototypes and the labelled issues, and each
    labelled issue's vote per chamber. Built once per process."""

    def __init__(self, embed_fn=embed):
        committees = jurisdictions()
        self.codes = {ch: [c for c in sorted(committees) if committees[c]["chamber"] == ch] for ch in CHAMBERS}
        items, owners = [], []
        for ch in CHAMBERS:
            for col, code in enumerate(self.codes[ch]):
                for subject in committees[code]["jurisdiction"]:
                    items.append(subject)
                    owners.append((ch, col))
        self.item_chamber = np.array([ch for ch, _ in owners])
        self.item_col = np.array([col for _, col in owners])
        self.items = embed_fn(items)
        exs = examples()
        self.examples = embed_fn([e["text"] for e in exs])
        self.dates = [e["date"] for e in exs]
        # votes[ch]: (examples, committees + 1); the last column is "none".
        self.votes = {}
        for ch in CHAMBERS:
            v = np.zeros((len(exs), len(self.codes[ch]) + 1))
            for i, e in enumerate(exs):
                labelled = [self.codes[ch].index(c) for c in e[ch] if c in self.codes[ch]]
                if labelled:
                    v[i, labelled] = 1.0 / len(labelled)
                else:
                    v[i, -1] = 1.0
            self.votes[ch] = v


class Shares(NamedTuple):
    """One chamber's shares for a batch of issues."""
    combined: np.ndarray  # (issues, committees + 1); the last column is "none"
    agree: np.ndarray  # (issues,) the two signals' top committees are the same


def shares(x: np.ndarray, ref: Reference, k: int, alpha: float, temperature: float,
           mask: np.ndarray | None = None) -> dict[str, Shares]:
    """{chamber: Shares}. `mask` (issues, examples) limits which labelled
    issues each issue may draw on — the calibration's held-out runs; None is
    all of them."""
    n, m = x.shape[0], ref.examples.shape[0]
    allowed = np.ones((n, m), bool) if mask is None else mask
    # Hubness: each subject's mean similarity to its HUB_K nearest reference
    # issues — every labelled issue any row may draw on (a held-out split's
    # training issues).
    ref_rows = ref.examples[allowed.any(axis=0)]
    item_ref = ref_rows @ ref.items.T
    hub = np.sort(item_ref, axis=0)[-min(HUB_K, len(ref_rows)):].mean(axis=0)
    item_scores = 2 * (x @ ref.items.T) - hub
    sims = np.where(allowed, x @ ref.examples.T, -np.inf)
    kk = min(k, int(allowed.sum(axis=1).min()))
    nearest = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
    weights = np.take_along_axis(sims, nearest, axis=1).clip(min=0.0)
    out = {}
    for ch in CHAMBERS:
        ncols = len(ref.codes[ch])
        in_ch = ref.item_chamber == ch
        proto = np.full((n, ncols), -np.inf)
        for col in range(ncols):
            proto[:, col] = item_scores[:, in_ch & (ref.item_col == col)].max(axis=1)
        z = temperature * (proto - proto.max(axis=1, keepdims=True))
        proto_share = np.exp(z) / np.exp(z).sum(axis=1, keepdims=True)
        knn = np.einsum("nk,nkc->nc", weights, ref.votes[ch][nearest]) / np.maximum(
            weights.sum(axis=1, keepdims=True), 1e-9)
        combined = (1 - alpha) * knn
        combined[:, :ncols] += alpha * proto_share
        agree = proto_share.argmax(axis=1) == knn[:, :ncols].argmax(axis=1)
        out[ch] = Shares(combined, agree)
    return out


def decide(s: Shares, threshold: float) -> np.ndarray:
    """Per issue, the routed committee column, or -1 to abstain. A committee
    is named only when both signals pick it (`agree`) and its combined share
    clears the threshold and beats the "none" vote. The agreement gate was
    chosen by measurement (scripts/calibrate_committee_routing.py): the
    threshold alone never lifted held-out precision past ~0.81, because
    nearly every wrong committee was named for a story no committee handles,
    and those are where the rules' wording and the labelled issues disagree."""
    combined = s.combined
    best = combined[:, :-1].argmax(axis=1)
    top = combined[np.arange(len(best)), best]
    return np.where(s.agree & (top >= threshold) & (top > combined[:, -1]), best, -1)


_reference: Reference | None = None


def route_committees(title: str, summary: str) -> list[dict]:
    """The committee in each chamber with jurisdiction over this issue, as
    {"code", "name", "chamber", "url"}; a chamber that abstains is left out."""
    global _reference
    if _reference is None:
        _reference = Reference()
    cal = calibration()
    x = embed([issue_text(title, summary)])
    committees = jurisdictions()
    routed = []
    for ch, s in shares(x, _reference, cal["k"], cal["alpha"], cal["temperature"]).items():
        col = int(decide(s, cal["threshold"])[0])
        if col >= 0:
            code = _reference.codes[ch][col]
            routed.append({"code": code, "name": committees[code]["name"], "chamber": ch,
                           "url": committees[code]["url"]})
    return routed
