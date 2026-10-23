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
     texts). The chamber's scores become shares by a softmax.
  2. Labelled texts (tier 3). The similarity-weighted committee votes of
     the k nearest labelled texts that vote in that chamber, the same kNN
     shape as the relevance gate's (policy_relevance.relevance_votes). A
     text whose story no committee handles votes "none".

combined = alpha·prototype share + (1 − alpha)·kNN share. The top committee
is named only when both signals pick it, combined clears the threshold and
it beats the "none" vote; otherwise the chamber abstains (decide). A wrong
committee misleads a reader in the one place the page tells them where to
go, so the fit favours precision.

The labels come from the data (2026-10-23, `labelled_texts`):

  - a bill's committee referrals, as Congress.gov records them in the
    bill's actions (each referral action names the committee's system
    code), against the bill's title and the first sentences of its CRS
    summary. The pipeline already stores those actions (api_cache, the
    bill path), so no request is added;
  - "none" from texts the data says are no committee's: national-feed
    articles their publisher filed under another desk that name no U.S.
    official or agency (FeedArticle label 0, the relevance gate's own
    label), and election coverage matched to a race (RaceCoverageItem),
    the campaign horse race the old rubric also called no committee's.

scripts/calibrate_committee_routing.py refits k, alpha, the temperature and
the threshold on those labels every day (data_refresh), held out on blocks
of consecutive days, and the refit replaces the fit in use only under the
acceptance rule (precision_rule.accept). Its reference (the labelled texts)
is installed beside it (committee_routing_reference.json) and its
embeddings are cached on the data volume, so a process builds the reference
without embedding twenty thousand texts. The figures are in the calibration
file and the script's docstring.

The hand-labelled issues (app/data/committee_routing_examples.json, 460
issues labelled 2026-10-11) are no longer fitted to. They are the reference
of the bundled fallback calibration, used until the first refit (a fresh
database holds no bills), and every refit reports itself against them as a
held-out check (`hand_labelled_check`), never as a choice.

Lives outside app/pipeline/, like live_results/, so it is outside the
analysis-code fingerprint without touching the hashed exemption lists:
nothing classified or scored reads it, and its only caller is the Action
Center (exempt, senate_pipeline._ACTION_CENTER_PATHS).
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import pathlib
import re
import tempfile
from collections.abc import Callable
from datetime import date, datetime
from typing import NamedTuple

import numpy as np
from sqlalchemy import select

from app import calibration_drift
from app import data_refresh
from app.atomic_write import runtime_data_path
from app.file_cache import files_stamp

CHAMBERS = ("house", "senate")
# CSLS's neighbourhood size, as in the paper (k = 10). Not fitted.
HUB_K = 10

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
JURISDICTIONS_PATH = _DATA_DIR / "committee_jurisdictions.json"
EXAMPLES_PATH = _DATA_DIR / "committee_routing_examples.json"
CALIBRATION_PATH = _DATA_DIR / "committee_routing_calibration.json"
REFERENCE_NAME = "committee_routing_reference.json"
EMBEDDINGS_NAME = "committee_routing_embeddings.npz"

# How much of a CRS summary a bill's text takes after its title: its first
# sentences say what the bill does, and the rest lists provisions.
SUMMARY_CHARS = 300
# How much of an article's description its text takes, as the relevance
# gate reads it (policy_relevance.DESCRIPTION_CHARS).
DESCRIPTION_CHARS = 200

_cache: dict = {}


def _load(path: pathlib.Path) -> dict:
    if path not in _cache:
        _cache[path] = json.loads(path.read_text())
    return _cache[path]


def jurisdictions() -> dict[str, dict]:
    # The refreshed copy when there is one (data_refresh: the chambers'
    # rules are re-read each Congress), else the bundled one.
    return data_refresh.read(JURISDICTIONS_PATH.name)["committees"]


def examples() -> list[dict]:
    return _load(EXAMPLES_PATH)["examples"]


def calibration() -> dict:
    # Refit daily from data-supplied labels (data_refresh), so the refreshed
    # copy wins; the bundled one is the hand-labelled fallback.
    return data_refresh.read(CALIBRATION_PATH.name)


def precision_rule_met(cal: dict | None = None) -> bool:
    """Whether the fit in use meets the Action Center's precision rule held
    out on data-supplied labels: the row is then worded as a fact, not a
    likely match. The bundled hand-labelled fit never did."""
    return bool((cal if cal is not None else calibration()).get("precision_rule_met"))


def data_hash() -> str:
    """Identifies the hand-labelled set and prototypes the bundled
    calibration was fitted to."""
    payload = json.dumps([
        [[e["text"], e["house"], e["senate"]] for e in examples()],
        {code: c["jurisdiction"] for code, c in sorted(jurisdictions().items())},
    ])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def issue_text(title: str, summary: str) -> str:
    """What routing embeds — the examples' `text` is built the same way."""
    return f"{title}. {summary or ''}".strip()


def embed(texts: list[str]) -> np.ndarray:
    from app.pipeline.vector_store import encode_normalized, get_similarity_model

    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    return np.asarray(encode_normalized(get_similarity_model(), list(texts)), dtype=np.float32)


# --- Data-supplied labels ---------------------------------------------------


class Labelled(NamedTuple):
    """One labelled text. `labels[chamber]` is the committee codes it votes
    for there, () for "none", or None when it says nothing about that
    chamber (a House bill never referred in the Senate)."""
    text: str
    labels: dict[str, tuple[str, ...] | None]
    day: int  # date ordinal
    kind: str  # "bill", "none", or "issue" (a hand-labelled one)


_BILL_KEY = re.compile(r"(\d+)-([a-z]+)-(\d+)$")


def _value(raw: str):
    doc = json.loads(raw)
    return doc["value"] if isinstance(doc, dict) and "value" in doc else doc


def _day(value) -> int | None:
    if isinstance(value, datetime):
        return value.date().toordinal()
    if isinstance(value, date):
        return value.toordinal()
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value[:10]).toordinal()
    return None


def _referral(action: dict) -> bool:
    # Congress.gov's own action types for a referral, or the referral's own
    # text (a record without a type).
    return action.get("type") in ("IntroReferral", "Referral") or (action.get("text") or "").startswith("Referred to")


def labelled_texts(db) -> list[Labelled]:
    """Every text the stored data labels: bills by their committee
    referrals, and "none" texts (module docstring). One entry per distinct
    text, its labels merged."""
    from app.models import ApiCache, FeedArticle, RaceCoverageItem, RepSponsoredBill, SponsoredBill

    codes = {code: c["chamber"] for code, c in jurisdictions().items()}
    bills: dict[str, dict] = {}

    def rows(pattern: str):
        return db.execute(select(ApiCache.cache_key, ApiCache.data_json).where(
            ApiCache.tier == "congress", ApiCache.cache_key.like(pattern)).execution_options(yield_per=500))

    for pattern in ("bill-actions-%", "bill-record-actions-%"):
        for key, raw in rows(pattern):
            m = _BILL_KEY.search(key)
            actions = _value(raw) if m else None
            if not isinstance(actions, list):
                continue
            bill = bills.setdefault("-".join(m.groups()), {"committees": set(), "day": None})
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if _referral(action):
                    for committee in action.get("committees") or []:
                        code = (committee.get("systemCode") or "")[:4].upper()
                        if code in codes:
                            bill["committees"].add(code)
                day = _day(action.get("actionDate"))
                if day and (bill["day"] is None or day < bill["day"]):
                    bill["day"] = day
    bills = {k: b for k, b in bills.items() if b["committees"]}
    for key, raw in rows("bill-record-bill-%"):
        m = _BILL_KEY.search(key)
        bill = bills.get("-".join(m.groups())) if m else None
        record = _value(raw) if bill is not None else None
        if isinstance(record, dict):
            bill["title"] = record.get("title") or bill.get("title")
            bill["day"] = _day(record.get("introducedDate")) or bill["day"]
    for key, raw in rows("bill-record-summaries-%"):
        m = _BILL_KEY.search(key)
        bill = bills.get("-".join(m.groups())) if m else None
        summaries = _value(raw) if bill is not None else None
        if isinstance(summaries, list) and summaries:
            # The first summary: the bill as introduced.
            first = min(summaries, key=lambda s: s.get("updateDate") or "")
            text = re.sub(r"<[^>]+>", " ", html.unescape(first.get("text") or ""))
            bill["summary"] = re.sub(r"\s+", " ", text).strip()
    for model in (SponsoredBill, RepSponsoredBill):
        for bill_id, title, congress in db.query(model.bill_id, model.title, model.congress):
            kind, _, number = (bill_id or "").partition(".")
            bill = bills.get(f"{congress}-{kind.lower()}-{number}")
            if bill is not None and not bill.get("title") and title:
                bill["title"] = title

    merged: dict[str, Labelled] = {}

    def add(text: str, labels: dict, day: int | None, kind: str) -> None:
        if not text or day is None:
            return
        old = merged.get(text)
        if old is not None:
            labels = {ch: None if old.labels[ch] is None and labels[ch] is None
                      else tuple(sorted(set(old.labels[ch] or ()) | set(labels[ch] or ())))
                      for ch in CHAMBERS}
            day = min(day, old.day)
            kind = old.kind if old.kind == kind else "bill"
        merged[text] = Labelled(text, labels, day, kind)

    for bill in bills.values():
        if not bill.get("title") or bill["day"] is None:
            continue
        text = f"{bill['title']}. {(bill.get('summary') or '')[:SUMMARY_CHARS]}".strip()
        labels = {ch: tuple(sorted(c for c in bill["committees"] if codes[c] == ch)) or None for ch in CHAMBERS}
        add(text, labels, bill["day"], "bill")
    none = {ch: () for ch in CHAMBERS}
    for title, description, published, seen in db.query(
            FeedArticle.title, FeedArticle.description, FeedArticle.published, FeedArticle.seen_at
    ).filter(FeedArticle.label == 0):
        add(f"{title}. {(description or '')[:DESCRIPTION_CHARS]}", none, _day(published) or _day(seen), "none")
    for title, summary, published, fetched in db.query(
            RaceCoverageItem.title, RaceCoverageItem.summary, RaceCoverageItem.published_at,
            RaceCoverageItem.fetched_at):
        add(f"{title}. {(summary or '')[:DESCRIPTION_CHARS]}", none, _day(published) or _day(fetched), "none")
    return sorted(merged.values(), key=lambda t: (t.day, t.text))


def hand_labelled() -> list[Labelled]:
    """The hand-labelled issues as labelled texts: the bundled fallback's
    reference, and every refit's held-out check."""
    return [Labelled(e["text"], {ch: tuple(e[ch]) for ch in CHAMBERS}, date.fromisoformat(e["date"]).toordinal(),
                     "issue") for e in examples()]


def reference_doc(texts: list[Labelled]) -> dict:
    """The labelled texts as the reference file stores them."""
    return {
        "_source": ("Generated by backend/scripts/calibrate_committee_routing.py with the calibration it was "
                    "fitted with: the texts the stored data labels (committee_routing.labelled_texts)."),
        "texts": [{"text": t.text, "day": t.day, "kind": t.kind,
                   **{ch: (list(t.labels[ch]) if t.labels[ch] is not None else None) for ch in CHAMBERS}}
                  for t in texts],
    }


def texts_from_doc(doc: dict) -> list[Labelled]:
    return [Labelled(t["text"], {ch: (tuple(t[ch]) if t.get(ch) is not None else None) for ch in CHAMBERS},
                     t["day"], t["kind"]) for t in doc.get("texts") or []]


def cached_embeddings(texts: list[str], embed_fn: Callable[[list[str]], np.ndarray] = embed,
                      write: bool = False) -> np.ndarray:
    """Each text's embedding, from the cache on the data volume where it
    holds one, embedding only the rest. `write` stores exactly these texts'
    embeddings as the new cache (the calibration script does; a serving
    process only reads)."""
    path = runtime_data_path(EMBEDDINGS_NAME)
    known: dict[str, np.ndarray] = {}
    try:
        with np.load(path) as z:
            known = dict(zip(z["keys"].tolist(), z["vectors"]))
    except (OSError, ValueError, KeyError):
        pass
    keys = [hashlib.sha256(t.encode()).hexdigest()[:24] for t in texts]
    missing = [i for i, k in enumerate(keys) if k not in known]
    if missing:
        for i, v in zip(missing, embed_fn([texts[i] for i in missing])):
            known[keys[i]] = np.asarray(v, dtype=np.float16)
    if not keys:
        return np.zeros((0, 384), np.float32)
    out = np.stack([known[k] for k in keys])
    if write and (missing or len(known) != len(set(keys))):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".committee_routing_embeddings.", suffix=".npz",
                                   dir=os.path.dirname(path))
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                np.savez(f, keys=np.array(keys), vectors=out.astype(np.float16))
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        finally:
            pathlib.Path(tmp).unlink(missing_ok=True)
    return out.astype(np.float32)


# --- The arithmetic -----------------------------------------------------------


def votes(texts: list[Labelled], codes: dict[str, list[str]]) -> dict[str, np.ndarray]:
    """Per chamber, (texts, committees + 1): each text's vote, the last
    column "none"; a row of zeros where a text says nothing about the
    chamber (it takes no part in that chamber's vote)."""
    out = {}
    for ch in CHAMBERS:
        v = np.zeros((len(texts), len(codes[ch]) + 1))
        for i, t in enumerate(texts):
            label = t.labels[ch]
            if label is None:
                continue
            cols = [codes[ch].index(c) for c in label if c in codes[ch]]
            if cols:
                v[i, cols] = 1.0 / len(cols)
            elif not label:
                v[i, -1] = 1.0
        out[ch] = v
    return out


class Reference:
    """Embeddings of the prototypes and the labelled texts, and each labelled
    text's vote per chamber. Built once per process. `texts` defaults to the
    hand-labelled issues (the bundled fallback)."""

    def __init__(self, embed_fn=embed, texts: list[Labelled] | None = None, vectors: np.ndarray | None = None):
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
        self.texts = texts if texts is not None else hand_labelled()
        self.examples = vectors if vectors is not None else embed_fn([t.text for t in self.texts])
        self.days = np.array([t.day for t in self.texts], dtype=int)
        self.kinds = np.array([t.kind for t in self.texts])
        self.votes = votes(self.texts, self.codes)


class Shares(NamedTuple):
    """One chamber's shares for a batch of issues."""
    combined: np.ndarray  # (issues, committees + 1); the last column is "none"
    agree: np.ndarray  # (issues,) the two signals' top committees are the same


class Neighbours(NamedTuple):
    """What shares needs from a batch, computed once for every setting: per
    chamber, the prototypes' corrected scores and the nearest labelled texts
    that vote there (most similar first)."""
    proto: dict[str, np.ndarray]  # (issues, committees)
    index: dict[str, np.ndarray]  # (issues, k_max): rows of ref.examples
    sims: dict[str, np.ndarray]  # (issues, k_max)


def neighbours(x: np.ndarray, ref: Reference, k_max: int, mask: np.ndarray | None = None) -> Neighbours:
    """`mask` (issues, texts) limits which labelled texts each issue may draw
    on (held-out runs); None is all of them."""
    n, m = x.shape[0], ref.examples.shape[0]
    allowed = np.ones((n, m), bool) if mask is None else mask
    # Hubness: each subject's mean similarity to its HUB_K nearest reference
    # texts — every labelled text any row may draw on.
    ref_rows = ref.examples[allowed.any(axis=0)]
    hub = np.sort(ref_rows @ ref.items.T, axis=0)[-min(HUB_K, len(ref_rows)):].mean(axis=0)
    item_scores = 2 * (x @ ref.items.T) - hub
    sims_all = x @ ref.examples.T
    proto, index, sims = {}, {}, {}
    for ch in CHAMBERS:
        in_ch = ref.item_chamber == ch
        proto[ch] = np.stack([item_scores[:, in_ch & (ref.item_col == col)].max(axis=1)
                              for col in range(len(ref.codes[ch]))], axis=1)
        eligible = allowed & (ref.votes[ch].sum(axis=1) > 0)[None, :]
        s = np.where(eligible, sims_all, np.float32(-np.inf))
        kk = max(1, min(k_max, int(eligible.sum(axis=1).min())))
        nearest = np.argpartition(-s, kk - 1, axis=1)[:, :kk]
        order = np.argsort(-np.take_along_axis(s, nearest, axis=1), axis=1, kind="stable")
        index[ch] = np.take_along_axis(nearest, order, axis=1)
        sims[ch] = np.take_along_axis(s, index[ch], axis=1)
    return Neighbours(proto, index, sims)


def shares_from(nb: Neighbours, ref: Reference, k: int, alpha: float, temperature: float) -> dict[str, Shares]:
    out = {}
    for ch in CHAMBERS:
        proto = nb.proto[ch]
        ncols = proto.shape[1]
        z = temperature * (proto - proto.max(axis=1, keepdims=True))
        proto_share = np.exp(z) / np.exp(z).sum(axis=1, keepdims=True)
        nearest = nb.index[ch][:, :k]
        near_sims = nb.sims[ch][:, :k]
        weights = np.where(np.isfinite(near_sims), near_sims, 0.0).clip(min=0.0)
        knn = np.einsum("nk,nkc->nc", weights, ref.votes[ch][nearest]) / np.maximum(
            weights.sum(axis=1, keepdims=True), 1e-9)
        combined = (1 - alpha) * knn
        combined[:, :ncols] += alpha * proto_share
        agree = proto_share.argmax(axis=1) == knn[:, :ncols].argmax(axis=1)
        out[ch] = Shares(combined, agree)
    return out


def shares(x: np.ndarray, ref: Reference, k: int, alpha: float, temperature: float,
           mask: np.ndarray | None = None) -> dict[str, Shares]:
    """{chamber: Shares}. `mask` (issues, texts) limits which labelled texts
    each issue may draw on — the calibration's held-out runs; None is all
    of them."""
    return shares_from(neighbours(x, ref, k, mask), ref, k, alpha, temperature)


def scores(s: Shares) -> tuple[np.ndarray, np.ndarray]:
    """(score, column) per issue: the top committee's combined share where
    decide could name it (both signals pick it and it beats "none"), else
    -1. decide names it when the score clears the threshold."""
    combined = s.combined
    best = combined[:, :-1].argmax(axis=1)
    top = combined[np.arange(len(best)), best]
    return np.where(s.agree & (top > combined[:, -1]), top, -1.0), best


def decide(s: Shares, threshold: float) -> np.ndarray:
    """Per issue, the routed committee column, or -1 to abstain. A committee
    is named only when both signals pick it (`agree`) and its combined share
    clears the threshold and beats the "none" vote. The agreement gate was
    chosen by measurement (scripts/calibrate_committee_routing.py): the
    threshold alone never lifted held-out precision past ~0.81, because
    nearly every wrong committee was named for a story no committee handles,
    and those are where the rules' wording and the labelled texts disagree."""
    score, best = scores(s)
    return np.where(score >= threshold, best, -1)


# --- Serving ------------------------------------------------------------------

_reference: tuple[object, Reference] | None = None


def reference_in_use(cal: dict, embed_fn=embed) -> Reference | None:
    """The reference the calibration in use was fitted with: the data's
    labelled texts (the reference file installed with a refit), else the
    hand-labelled issues; None when a refit's reference file is missing.
    Rebuilt when the reference file is rewritten."""
    global _reference
    data = cal.get("reference") == "data"
    stamp = (data, files_stamp([runtime_data_path(REFERENCE_NAME)]) if data else None)
    if _reference is None or _reference[0] != stamp:
        if data:
            texts = texts_from_doc(data_refresh.read(REFERENCE_NAME))
            ref = Reference(embed_fn, texts, cached_embeddings([t.text for t in texts], embed_fn)) if texts else None
        else:
            ref = Reference(embed_fn)
        _reference = (stamp, ref)
    return _reference[1]


def route_committees(title: str, summary: str) -> list[dict]:
    """The committee in each chamber with jurisdiction over this issue, as
    {"code", "name", "chamber", "url", "likely"}; a chamber that abstains is
    left out. `likely` is set while the fit in use falls short of the
    precision rule (precision_rule_met)."""
    cal = calibration()
    ref = reference_in_use(cal)
    if ref is None:
        # A refit's parameters belong to its reference, which is missing:
        # the bundled fit and its hand-labelled reference decide.
        cal = _load(CALIBRATION_PATH)
        ref = reference_in_use(cal)
    x = embed([issue_text(title, summary)])
    committees = jurisdictions()
    likely = not precision_rule_met(cal)
    routed = []
    for ch, s in shares(x, ref, cal["k"], cal["alpha"], cal["temperature"]).items():
        col = int(decide(s, cal["threshold"])[0])
        # Routed (1) or abstained (0): the share of issues routed is what
        # drifts when the stream moves away from the labelled texts.
        calibration_drift.observe("committee_routing", [float(col >= 0)], 0.5)
        if col >= 0:
            code = ref.codes[ch][col]
            routed.append({"code": code, "name": committees[code]["name"], "chamber": ch,
                           "url": committees[code]["url"], "likely": likely})
    return routed
