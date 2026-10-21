"""Which national-feed articles are about U.S. policy: the Action Center's
relevance gate, a kNN vote over articles the publishers themselves filed.

The reference set labels itself. Every article the gate judges is stored
(FeedArticle) with the label its publisher's own filing gives it:

  - 1: the publisher filed it under U.S. politics (a politics-only feed, or
    a politics desk's URL section: news_feeds.publisher_scope), or it names
    a U.S. government actor Civitas keeps a record of (a sitting member of
    Congress, the sitting president, a sitting justice, a federal agency as
    the Federal Register lists it: OfficialNames);
  - 0: the publisher filed it under another desk (a world feed, a foreign
    or arts desk) and it names none of them;
  - no label: the publisher said neither.

An article is kept when the similarity-weighted share of positives among
its k nearest labelled articles reaches the threshold (Cover & Hart 1967).
k and the threshold are refit once a UTC day on the stored reference
(calibration): leave one out, each article voted on by the others (a copy of
the same wire story, cosine above NEAR_DUPLICATE, left out of its own vote),
the setting with the best F0.5 (precision weighed over recall, the Action
Center's fewer-but-better aim), ties to the lower threshold. Nothing in it
is labelled by hand, and nothing about it is frozen on a date.

Measured 2026-10-16 against the hand-labelled set this replaced, on the
same three temporal splits it was calibrated on: precision 0.88/0.94/0.96
against the hand-label vote's 0.89/0.93/0.98 (inside each other's 95%
intervals), recall 0.76-0.83 against 0.84-0.88. The replay of every issue
published 2026-10-02 to 10-15 is in docs/research/action-center-relevance.md.

Officials' names are masked ("[name]") in what is embedded, both in the
reference and in what is judged: the vote should read what kind of story it
is, not who is in it, and the masked and unmasked texts of one story sit far
apart (until 2026-10-16 the reference was masked and the judged text was
not).

On its first run (an empty store) the gate backfills the store from the
national-feed articles the database already holds with their URLs
(backfill_from_history: the election coverage's race_coverage_items, read
from the same feeds), labelled the same way. On 2026-10-16 that was 443
labelled articles, 440 of them relevant: the database keeps what the old
gate let through and the races matched, so it holds almost no articles
filed under another desk. Those arrive with the feeds themselves, about 30
a day (48 in a first run's 48-hour window).

Until the store holds as many articles of each label as the seed does, the
seed — the texts of app/data/policy_relevance_examples.json, labelled the
same way from their outlet and masked names, never from the hand labels
they once carried — fills in, so a fresh database still has a gate. The
store takes over once its own evidence for each label is at least the
seed's: no count is typed (it was 150, "a guess at enough").

Lives outside app/pipeline/, like committee_routing.py, so it is outside the
analysis-code fingerprint without touching the hashed exemption lists:
nothing classified or scored reads it, and its callers are the Action Center
and the trend gates (both exempt, senate_pipeline._ACTION_CENTER_PATHS).
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy.orm import Session

from app.models import Agency, FeedArticle, Justice, President, RaceCoverageItem, Representative, Senator
from app.pipeline import vector_store
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.news_feeds import NEWS_FEEDS, OTHER_SCOPE, POLITICS_SCOPE, NewsArticle, publisher_scope
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
SEED_PATH = _DATA_DIR / "policy_relevance_examples.json"

# How much of the description the gate reads after the headline. The first
# sentence says who did what; past it a description wanders.
DESCRIPTION_CHARS = 200

# How long a stored article stays in the reference. Long enough to hold
# several news cycles of every desk; short enough that the vote follows
# what the feeds carry now.
REFERENCE_DAYS = 90

# A copy of the same wire story (PBS republishing AP) is not independent
# evidence for itself when the calibration leaves one out.
NEAR_DUPLICATE = 0.95

K_GRID = (5, 9, 15, 25, 41)
THRESHOLD_GRID = tuple(round(t, 2) for t in np.arange(0.30, 0.96, 0.05))
F_BETA = 0.5

_CALIBRATION_TIER = "policy-relevance"
_CALIBRATION_KEY = "calibration"
_CALIBRATION_TTL_HOURS = 24 * 400

NAME_MASK = "[name]"

# Stored with each embedding, so a model change starts a fresh reference
# rather than comparing vectors from two spaces.
_MODEL = vector_store._SIMILARITY_MODEL_NAME


def gate_text(article: NewsArticle) -> str:
    return f"{article.title}. {(article.summary or '')[:DESCRIPTION_CHARS]}"


def embed(texts: Sequence[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    return vector_store.encode_normalized(vector_store.get_similarity_model(), list(texts)).astype(np.float32)


def relevance_votes(
    article_embeddings: np.ndarray, reference: np.ndarray, labels: np.ndarray, k: int,
) -> np.ndarray:
    """Similarity-weighted share of positive examples among each article's k
    nearest (unit vectors, so the dot product is the cosine). Negative
    similarities weigh nothing; an article with no positive neighbour
    scores 0."""
    sims = article_embeddings @ reference.T
    k = min(k, reference.shape[0])
    nearest = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    weights = np.take_along_axis(sims, nearest, axis=1).clip(min=0.0)
    relevant = labels[nearest]
    return (weights * relevant).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)


class OfficialNames:
    """The U.S. government actors Civitas tracks, as text patterns.

    Masked: each sitting member's and sitting justice's full name and its
    first-and-last form, and the sitting president's full name and surname
    (as action_center._find_related_officials attributes a story to the
    president). A member's bare surname is not looked for in an article:
    a longer text holds more namesakes (action_center._find_related_senators),
    and a surname is often also a place, a storm or a common word.

    Named but not masked: a federal agency, by its Federal Register name
    (Agency.name) or its all-capitals short name (ICE, FEMA; 3-5 letters,
    and not the agency's whole name, as "ACTION" is). An agency's name says
    what a story is about; a person's says who."""

    def __init__(self, names: Iterable[str], president: str | None = None,
                 agencies: Iterable[tuple[str, str | None]] = ()):
        forms: set[str] = set()
        for name in (n.strip() for n in names if n and n.strip()):
            parts = name.split()
            forms.add(name)
            if len(parts) > 2:
                forms.add(f"{parts[0]} {parts[-1]}")
        if president and president.strip():
            forms.update({president.strip(), president.split()[-1]})
        self._pattern = _alternation(forms, 0)
        agency_names, short_names = set(), set()
        for name, short in agencies:
            if name and len(name) > 8:
                agency_names.add(name)
            if short and short.isalpha() and short.isupper() and 3 <= len(short) <= 5 and short != (name or "").upper():
                short_names.add(short)
        self._agency = _alternation(agency_names, re.IGNORECASE)
        self._agency_short = _alternation(short_names, 0)

    @classmethod
    def from_db(cls, db: Session) -> OfficialNames:
        names = [n for (n,) in db.query(Senator.name).filter(Senator.is_current.is_(True))]
        names += [n for (n,) in db.query(Representative.name).filter(Representative.is_current.is_(True))]
        names += [n for (n,) in db.query(Justice.name).filter(Justice.is_active.is_(True))]
        president = db.query(President.name).filter(President.is_current.is_(True)).scalar()
        agencies = db.query(Agency.name, Agency.short_name).all()
        return cls(names, president, agencies)

    def mask(self, text: str) -> tuple[str, bool]:
        """TEXT with every tracked person's name replaced by NAME_MASK, and
        whether it names a tracked person or a federal agency."""
        masked, n = self._pattern.subn(NAME_MASK, text) if self._pattern else (text, 0)
        agency = any(p is not None and p.search(text) for p in (self._agency, self._agency_short))
        return masked, n > 0 or agency


def _alternation(forms: Iterable[str], flags: int) -> re.Pattern | None:
    """One pattern matching any of FORMS as whole words, longest first."""
    ordered = sorted(set(forms), key=len, reverse=True)
    if not ordered:
        return None
    return re.compile(r"(?<![\w'])(" + "|".join(re.escape(f) for f in ordered) + r")(?!\w)", flags)


def publisher_label(scope: str | None, names_official: bool) -> int | None:
    """1, 0 or None, as the module docstring defines them."""
    if scope == POLITICS_SCOPE or names_official:
        return 1
    if scope == OTHER_SCOPE:
        return 0
    return None


# The seed's outlets, filed as NEWS_FEEDS files them. Its NPR rows came from
# both of NPR's feeds under one name and its PBS and Hill rows carry no URL,
# so those outlets' rows are labelled by masked names alone.
_SEED_SCOPES = {"Politico": POLITICS_SCOPE, "Roll Call": POLITICS_SCOPE,
                "BBC World": OTHER_SCOPE, "AP News": OTHER_SCOPE}


def seed_rows() -> list[tuple[str, int]]:
    """(text, label) for the seed: its texts, labelled from their outlet and
    their masked names (module docstring)."""
    rows = []
    for e in json.loads(SEED_PATH.read_text())["examples"]:
        label = publisher_label(_SEED_SCOPES.get(e["source"]), NAME_MASK in e["text"])
        if label is not None:
            rows.append((e["text"], label))
    return rows


_seed_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _seed_reference() -> tuple[np.ndarray, np.ndarray]:
    key = id(vector_store.get_similarity_model())
    if key not in _seed_cache:
        rows = seed_rows()
        _seed_cache[key] = (embed([t for t, _ in rows]), np.array([lab for _, lab in rows], dtype=float))
    return _seed_cache[key]


def reference(db: Session, exclude_urls: Iterable[str] = ()) -> tuple[np.ndarray, np.ndarray, int]:
    """(embeddings, labels, seed rows used) of the labelled reference: every
    stored labelled article inside REFERENCE_DAYS (but those in
    `exclude_urls`, which are being judged) on the current model, plus the
    seed while the store holds fewer articles of either label than the seed
    has of it."""
    model = _MODEL
    since = utcnow() - timedelta(days=REFERENCE_DAYS)
    exclude = set(exclude_urls)
    rows = (db.query(FeedArticle.url, FeedArticle.embedding, FeedArticle.label)
            .filter(FeedArticle.label.isnot(None), FeedArticle.model == model, FeedArticle.seen_at >= since)
            .all())
    rows = [r for r in rows if r.url not in exclude]
    vectors = np.array([np.frombuffer(r.embedding, dtype=np.float32) for r in rows], dtype=np.float32)
    labels = np.array([float(r.label) for r in rows])
    positives = int(labels.sum()) if len(rows) else 0
    seed = [label for _, label in seed_rows()]
    seed_positives, seed_negatives = sum(seed), len(seed) - sum(seed)
    if positives >= seed_positives and len(rows) - positives >= seed_negatives:
        return vectors, labels, 0
    seed_vectors, seed_labels = _seed_reference()
    if not rows:
        return seed_vectors, seed_labels, len(seed_labels)
    return np.vstack([vectors, seed_vectors]), np.concatenate([labels, seed_labels]), len(seed_labels)


def _leave_one_out_votes(vectors: np.ndarray, labels: np.ndarray, k_max: int, chunk: int = 512) -> tuple[
        np.ndarray, np.ndarray]:
    """For each row, its k_max nearest other rows (near-duplicates left out)
    and their similarities, best first. Chunked so memory stays at
    chunk x rows."""
    n = len(labels)
    k_max = min(k_max, n - 1)
    nearest = np.zeros((n, k_max), dtype=np.int64)
    sims_out = np.zeros((n, k_max), dtype=np.float32)
    for start in range(0, n, chunk):
        block = vectors[start:start + chunk] @ vectors.T
        rows = np.arange(start, min(start + chunk, n))
        block[np.arange(len(rows)), rows] = -np.inf
        block[block > NEAR_DUPLICATE] = -np.inf
        top = np.argpartition(-block, k_max - 1, axis=1)[:, :k_max]
        top_sims = np.take_along_axis(block, top, axis=1)
        order = np.argsort(-top_sims, axis=1)
        nearest[rows] = np.take_along_axis(top, order, axis=1)
        sims_out[rows] = np.take_along_axis(top_sims, order, axis=1)
    return nearest, sims_out


def _precision_recall(truth: np.ndarray, kept: np.ndarray) -> tuple[float, float]:
    tp = float((truth & kept).sum())
    return tp / max(float(kept.sum()), 1.0), tp / max(float(truth.sum()), 1.0)


def fit(vectors: np.ndarray, labels: np.ndarray) -> dict:
    """k and the threshold for this reference (module docstring), with the
    leave-one-out precision and recall at them."""
    truth = labels >= 0.5
    nearest, sims = _leave_one_out_votes(vectors, labels, max(K_GRID))
    weights_all = np.where(np.isfinite(sims), sims, 0.0).clip(min=0.0)
    best = None
    b2 = F_BETA * F_BETA
    for k in K_GRID:
        w = weights_all[:, :k]
        votes = (w * labels[nearest[:, :k]]).sum(axis=1) / np.maximum(w.sum(axis=1), 1e-9)
        for t in THRESHOLD_GRID:
            p, r = _precision_recall(truth, votes >= t)
            f = (1 + b2) * p * r / max(b2 * p + r, 1e-12)
            # Strictly better only: ties keep the lower threshold (grid order).
            if best is None or f > best[0] + 1e-12:
                best = (f, k, float(t), p, r)
    f, k, t, p, r = best
    return {"k": k, "threshold": t, "f0_5": round(f, 3), "precision": round(p, 3), "recall": round(r, 3),
            "rows": int(len(labels)), "positives": int(truth.sum())}


def calibration(db: Session, vectors: np.ndarray, labels: np.ndarray, seeded: int) -> dict:
    """Today's fit, refit on the first run of each UTC day (or when the
    model changed) and kept in api_cache."""
    today = utcnow().date().isoformat()
    model = _MODEL
    stored = api_cache_get(db, _CALIBRATION_TIER, _CALIBRATION_KEY, max_age_hours=_CALIBRATION_TTL_HOURS)
    if stored and stored.get("date") == today and stored.get("model") == model:
        return stored
    fitted = fit(vectors, labels)
    fitted.update({"date": today, "model": model, "seed_rows": seeded})
    # In a savepoint and uncommitted, like _store: the refresh commits.
    try:
        with db.begin_nested():
            api_cache_set(db, _CALIBRATION_TIER, _CALIBRATION_KEY, fitted,
                          normal_ttl_hours=_CALIBRATION_TTL_HOURS, commit=False)
    except Exception:
        logger.warning("Relevance calibration not stored; refit next run", exc_info=True)
    logger.info("Relevance gate refit: %s", fitted)
    return fitted


def _feed_url_for(source_name: str) -> str:
    """The NEWS_FEEDS url of a source with exactly one feed, else "" (NPR's
    two desks share a name, so a stored NPR article's desk is unknown)."""
    urls = [f["url"] for f in NEWS_FEEDS if f["name"] == source_name]
    return urls[0] if len(urls) == 1 else ""


def backfill_from_history(db: Session, names: OfficialNames) -> int:
    """Fill an empty store from the national-feed articles the database
    already holds (module docstring), inside a savepoint (the caller
    commits). Returns how many were stored; a store that already has rows
    is left alone."""
    if db.query(FeedArticle.id).first() is not None:
        return 0
    sources = {f["name"] for f in NEWS_FEEDS}
    rows = (db.query(RaceCoverageItem.url, RaceCoverageItem.title, RaceCoverageItem.summary,
                     RaceCoverageItem.source_name, RaceCoverageItem.published_at, RaceCoverageItem.fetched_at)
            .filter(RaceCoverageItem.source_name.in_(sources)).all())
    since = utcnow() - timedelta(days=REFERENCE_DAYS)
    articles, seen = [], {}
    for r in rows:
        when = r.published_at or r.fetched_at
        if not r.url or r.url in seen or (when and when < since):
            continue
        a = NewsArticle(title=r.title or "", url=r.url, source_name=r.source_name, summary=r.summary or "",
                        published=when, feed_url=_feed_url_for(r.source_name))
        seen[r.url] = when
        articles.append(a)
    if not articles:
        return 0
    masked = [names.mask(gate_text(a)) for a in articles]
    vectors = embed([m for m, _ in masked])
    _store(db, articles, masked, vectors, None, None, seen_at={a.url: seen[a.url] for a in articles})
    logger.info("Relevance store backfilled with %d stored national-feed articles", len(articles))
    return len(articles)


def judge(db: Session, articles: Sequence[NewsArticle]) -> tuple[list[tuple[NewsArticle, np.ndarray, float]], dict]:
    """((article, embedding, vote) for the articles the gate keeps, in input
    order; the fit it used). Every judged article is stored with its
    publisher label."""
    if not articles:
        return [], {}
    names = OfficialNames.from_db(db)
    try:
        backfill_from_history(db, names)
    except Exception:
        logger.warning("Relevance store backfill failed; the seed covers the gap", exc_info=True)
    masked = [names.mask(gate_text(a)) for a in articles]
    vectors = embed([m for m, _ in masked])
    reference_vectors, reference_labels, seeded = reference(db, exclude_urls=[a.url for a in articles])
    fitted = calibration(db, reference_vectors, reference_labels, seeded)
    votes = relevance_votes(vectors, reference_vectors, reference_labels, int(fitted["k"]))
    threshold = float(fitted["threshold"])
    _store(db, articles, masked, vectors, votes, threshold)
    return [(a, vectors[i], float(votes[i])) for i, a in enumerate(articles) if votes[i] >= threshold], fitted


def _store(db: Session, articles: Sequence[NewsArticle], masked: Sequence[tuple[str, bool]],
           vectors: np.ndarray, votes: np.ndarray | None, threshold: float | None,
           seen_at: dict[str, datetime] | None = None) -> None:
    """Upsert each judged article by URL and prune past REFERENCE_DAYS,
    inside a savepoint of the caller's session: a failure here rolls back
    only these rows, never the refresh's own pending work, and costs the
    reference one run's articles, never the run. The caller commits."""
    model = _MODEL
    now = utcnow()
    try:
        with db.begin_nested():
            existing = {r.url: r for r in
                        db.query(FeedArticle).filter(FeedArticle.url.in_([a.url for a in articles]))}
            for i, (a, (text, named), vector) in enumerate(zip(articles, masked, vectors)):
                vote = None if votes is None else float(votes[i])
                scope = publisher_scope(a)
                row = existing.get(a.url)
                if row is None:
                    row = FeedArticle(url=a.url, seen_at=(seen_at or {}).get(a.url) or now)
                    db.add(row)
                    existing[a.url] = row
                row.source_name = a.source_name
                row.title = a.title or ""
                row.description = a.summary or ""
                row.scope = scope
                row.text = text
                row.names_official = named
                row.label = publisher_label(scope, named)
                row.vote = vote
                row.kept = vote is not None and vote >= threshold
                row.embedding = np.asarray(vector, dtype=np.float32).tobytes()
                row.model = model
                row.published = a.published.replace(tzinfo=None) if a.published else None
            db.query(FeedArticle).filter(FeedArticle.seen_at < now - timedelta(days=REFERENCE_DAYS)).delete(
                synchronize_session=False)
    except Exception:
        logger.warning("Feed articles not stored for the relevance reference", exc_info=True)
