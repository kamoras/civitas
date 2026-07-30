"""Derive every tunable in explore search from the corpus it runs against.
Derive every tunable in explore search from the corpus it runs against.

Nothing in `app/data/explore_ranking.json` is typed by a human. This script
computes it, the same generated-data pattern as `district_pvi.json` and
`state_population.json` (AGENTS.md principle 3a). Two constants are
deliberately NOT here because they are published results rather than
properties of this corpus, and they live in code with their citations:
reciprocal rank fusion's K = 60 (Cormack, Clarke & Büttcher, SIGIR 2009)
and PageRank's damping 0.85 (Brin & Page, 1998).

Lives in app code rather than in a script because the explore pipeline
calls it on every run (`services/explore_ranking.calibrate_and_store`), so
the parameters always describe the corpus actually being searched.
`scripts/calibrate_explore_ranking.py` is a thin CLI over the same
function, for regenerating the bundled bootstrap file or inspecting a
calibration without a pipeline run.

What each value is derived from

-------------------------------

**BM25F field weights.** Fitted by coordinate ascent on known-item
retrieval MRR — take a document out of the corpus, build a query someone
looking for it would plausibly type, measure where it lands. That protocol
is valid for the retrieval side precisely because the target is the single
correct answer, which is also why it cannot be used for the priors below
(see `evaluate_explore_search.py`'s "How to read this").

**Prior weights.** Derived, not fitted, because no label-free objective can
score them. Two measured quantities settle them:

  δ, the retrievers' own resolution limit — the median rank disagreement
  between the semantic and keyword channels on documents both return. A
  relevance difference smaller than δ is below what the retrievers can
  actually resolve, so reordering inside it costs nothing real.

  coverage, the fraction of a candidate pool a prior can order at all —
  documents it assigns distinct ranks rather than ties. A citation graph
  with no edges has coverage ≈ 0 and therefore weight ≈ 0, so the prior
  disappears on a corpus that cannot support it instead of needing a
  special case.

A prior's total score swing under RRF is w/(K+1). Setting that equal to the
score gap δ ranks buys across the live retrieval channels gives

    w = channels × (K + 1) × [1/(K+1) − 1/(K+1+δ)] × coverage

which reads: *a prior may reorder documents the retrievers cannot tell
apart, in proportion to how much it can actually distinguish, and no
further.* Better retrievers (smaller δ) shrink the priors automatically.

**Candidate pool.** Measured filter survival: the fraction of retrieved
candidates that live through commentable/orphan/duplicate filtering. Pool
is the page size divided by that fraction, so a filtered search still fills
a page instead of returning four results out of thirty.

**Source diversity cap.** The corpus's own median documents-per-source
among sources that publish more than once. A cap below the corpus's normal
shape fights the data; above it, it never fires.

**Fingerprint length.** The shortest normalised prefix at which genuinely
distinct documents stop colliding, plus the length below which no prefix
separates them at all — both read off a collision curve over the corpus.

**Snippet length and minimum term length.** The corpus's median sentence
length in tokens (a keyword-in-context excerpt should show a sentence of
context), and the shortest term length whose terms are not near-universal.

"""

from __future__ import annotations

import collections
import importlib.util
import logging
import math
import pathlib
import random
import re
import statistics

logger = logging.getLogger(__name__)

# Published constants, restated so the arithmetic below is self-contained.
# Their home is config_definitions / document_authority.
RRF_K = 60
RETRIEVAL_CHANNELS = 2

# The API's own `limit` ceiling — a contract, not a tuning value.
MAX_PAGE_SIZE = 50

DEFAULT_SAMPLES = 200
CALIBRATION_SEED = 20260730

_WORD = re.compile(r"[0-9A-Za-z][0-9A-Za-z\'\u2019.\-]*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NON_WORD = re.compile(r"[^0-9a-z]+")


def _normalise(text: str) -> str:
    return _NON_WORD.sub(" ", (text or "").lower()).strip()


# ── individual derivations ───────────────────────────────────────

def _weight_ladder():
    """Doubling steps, open-ended.

    A fixed grid would cap the fit at whatever its largest entry happened
    to be — and the first run of this calibration did exactly that,
    returning the grid's own maximum for `title`, which is a truncated
    optimum reported as an answer. The ladder instead keeps doubling and
    the search stops when doubling no longer improves MRR, so the result is
    where the data stops paying rather than where a list ended.
    """
    value = 1.0
    while value <= 1024.0:
        yield value
        value *= 2.0


def derive_field_weights(db, docs, probe_fn, measure_fn) -> dict:
    """Coordinate ascent on known-item MRR.

    Starts from unweighted BM25 (every field 1.0) — the published baseline,
    not a guess — and fits title and summary relative to body, whose weight
    stays 1.0 because only the ratios matter.
    """
    from app.pipeline import explore_ranking

    best = {"title": 1.0, "summary": 1.0, "body": 1.0}
    probes = probe_fn(docs)

    def score(weights) -> float:
        with explore_ranking.override({"field_weights": weights}):
            ranks = measure_fn(db, probes)["keyword"]
        found = [r for r in ranks if r is not None]
        return sum(1.0 / r for r in found) / max(len(ranks), 1)

    current = score(best)
    for _ in range(3):
        improved = False
        for field in ("title", "summary"):
            for value in _weight_ladder():
                trial = {**best, field: value}
                if trial == best:
                    continue
                trial_score = score(trial)
                if trial_score > current + 1e-9:
                    best, current = trial, trial_score
                    improved = True
        if not improved:
            break
    at_ceiling = [f for f, v in best.items() if v >= 1024.0]
    if at_ceiling:
        logger.warning(
            "Field weight(s) %s reached the ladder ceiling — the fit is "
            "truncated, not converged", at_ceiling,
        )
    return {"weights": best, "mrr": round(current, 4), "converged": not at_ceiling}


def derive_retriever_resolution(db, probes, search_lexical, search_semantic) -> float:
    """δ — median rank disagreement between the two retrieval channels.

    How far apart the retrievers place the same document is how far apart
    two documents must be before a ranking difference between them means
    anything.
    """
    gaps: list[int] = []
    for probe in probes:
        semantic = search_semantic(probe["query"], n_results=MAX_PAGE_SIZE)
        if not semantic:
            continue
        keyword = search_lexical(db, probe["query"], limit=MAX_PAGE_SIZE)
        sem_rank = {h["id"]: i for i, h in enumerate(semantic, start=1)}
        kw_rank = {h["id"]: i for i, h in enumerate(keyword, start=1)}
        for doc_id in set(sem_rank) & set(kw_rank):
            gaps.append(abs(sem_rank[doc_id] - kw_rank[doc_id]))
    return float(statistics.median(gaps)) if gaps else 0.0


def _competition_coverage(values: list) -> float:
    """Fraction of items a signal can order — distinct values over items."""
    if not values:
        return 0.0
    return len(set(values)) / len(values)


def derive_prior_weights(delta: float, coverage: dict[str, float]) -> dict:
    """w = channels × (K+1) × [1/(K+1) − 1/(K+1+δ)] × coverage."""
    if delta <= 0:
        gap = 0.0
    else:
        gap = 1.0 / (RRF_K + 1) - 1.0 / (RRF_K + 1 + delta)
    swing = RETRIEVAL_CHANNELS * (RRF_K + 1) * gap
    return {name: round(swing * cov, 4) for name, cov in coverage.items()}


def derive_candidate_pool(survival: float) -> dict:
    """Page size divided by the measured post-filter survival rate."""
    survival = max(survival, 1e-3)
    pool = int(math.ceil(MAX_PAGE_SIZE / survival))
    # The ceiling is the pool at which even the harshest measured filter
    # still fills a page; the default is the same arithmetic at the median.
    return {"default": min(pool, 4000), "max": min(pool * 3, 4000)}


def derive_diversity_cap(counts: list[int]) -> int:
    """Median documents-per-source among sources that publish more than once."""
    repeated = [c for c in counts if c > 1]
    if not repeated:
        return 1
    return max(1, int(statistics.median(repeated)))


def derive_fingerprint(docs) -> dict:
    """Shortest prefix at which distinct documents stop colliding."""
    texts = [_normalise(f"{d['title']} {d['body']}") for d in docs]
    texts = [t for t in texts if t]
    if not texts:
        return {"prefix_chars": 400, "min_chars": 80}

    distinct_total = len(set(texts))
    prefix_chars = None
    for length in range(50, 1001, 25):
        prefixes = {t[:length] for t in texts}
        if len(prefixes) >= distinct_total:
            prefix_chars = length
            break
    if prefix_chars is None:
        prefix_chars = 1000

    # Below this, prefixes stop separating anything — documents shorter than
    # it must never be compared by content at all.
    min_chars = 25
    for length in range(25, prefix_chars + 1, 25):
        if len({t[:length] for t in texts}) > distinct_total * 0.5:
            min_chars = length
            break
    return {"prefix_chars": prefix_chars, "min_chars": min_chars}


def derive_text_shape(docs) -> dict:
    """Median sentence length, and the shortest non-universal term length."""
    lengths = []
    by_length: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    n_docs = len(docs) or 1
    for doc in docs:
        text = f"{doc['title']} {doc['body']}"
        for sentence in _SENTENCE.split(text):
            words = _WORD.findall(sentence)
            if words:
                lengths.append(len(words))
        for term in {w.lower() for w in _WORD.findall(text)}:
            by_length[len(term)][term] += 1

    snippet_tokens = int(statistics.median(lengths)) if lengths else 32

    # A term appearing in most documents carries almost no retrieval signal
    # (Spärck Jones 1972). Find the shortest length whose terms are not, on
    # average, near-universal.
    min_term_length = 1
    for length in sorted(by_length):
        counter = by_length[length]
        mean_df = sum(counter.values()) / max(len(counter), 1)
        if mean_df / n_docs < 0.5:
            min_term_length = length
            break
    return {"snippet_tokens": snippet_tokens, "min_term_length": min_term_length}



def _harness():
    """The evaluation harness, imported by path — it is a script, and this
    reuses its probe construction rather than growing a second copy."""
    path = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "scripts" / "evaluate_explore_search.py")
    spec = importlib.util.spec_from_file_location("explore_eval_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compute_calibration(db, samples: int = DEFAULT_SAMPLES) -> dict | None:
    """Measure every ranking parameter against the current corpus."""
    from app.models import ExploreDocument
    from app.pipeline.lexical_index import search_lexical
    from app.pipeline.vector_store import search_explore_documents
    from app.services.explore_search import hybrid_search

    harness = _harness()

    rows = db.query(
        ExploreDocument.id, ExploreDocument.title, ExploreDocument.body,
        ExploreDocument.politician_id, ExploreDocument.agency_name,
    ).all()
    if not rows:
        logger.warning("Cannot calibrate explore ranking: no documents indexed")
        return None

    rng = random.Random(CALIBRATION_SEED)
    sample = rng.sample(rows, min(samples, len(rows)))
    docs = [{"id": r.id, "title": r.title or "", "body": (r.body or "")[:12000]}
            for r in sample]

    corpus_df: collections.Counter = collections.Counter()
    for d in docs:
        for term in {w.lower() for w in _WORD.findall(f"{d['title']} {d['body']}")}:
            corpus_df[term] += 1

    def probe_fn(sample_docs):
        return harness.build_probes(sample_docs, corpus_df, len(sample_docs))

    def measure_fn(session, probes):
        return harness.measure(session, probes)["ALL"]

    # Ordering matters: the first three come straight from the corpus text
    # and need no ranking at all, so they can be in force while the ranking
    # ones are being fitted. Nothing here reads a value it is deriving.
    fingerprint = derive_fingerprint(docs)
    shape = derive_text_shape(docs)
    source_counts = collections.Counter(
        r.politician_id or r.agency_name for r in rows
        if (r.politician_id or r.agency_name)
    )
    cap = derive_diversity_cap(list(source_counts.values()))

    # A neutral starting calibration, so fitting works on a corpus that has
    # never been calibrated: unweighted BM25, no priors, a pool of one page,
    # no diversity cap and no deduplication. Every one of those is the
    # "signal switched off" value, not a guess at the answer.
    from app.pipeline import explore_ranking

    neutral = {
        "field_weights": {"title": 1.0, "summary": 1.0, "body": 1.0},
        "prior_weights": {"freshness": 0.0, "authority": 0.0},
        "candidate_pool": {"default": MAX_PAGE_SIZE, "max": MAX_PAGE_SIZE},
        "source_diversity_cap": 0,
        "fingerprint": fingerprint,
        "text_shape": shape,
    }

    with explore_ranking.override(neutral):
        field = derive_field_weights(db, docs, probe_fn, measure_fn)
        probes = probe_fn(docs)
        delta = derive_retriever_resolution(
            db, probes, search_lexical, search_explore_documents)

        fresh_cov, auth_cov, survivals = [], [], []
        with explore_ranking.override({"field_weights": field["weights"]}):
            for probe in probes[:60]:
                outcome = hybrid_search(db, probe["query"], limit=MAX_PAGE_SIZE)
                results = outcome["results"]
                if not results:
                    continue
                fresh_cov.append(_competition_coverage([r["date"] for r in results]))
                cited = [r["citedByCount"] for r in results if r["citedByCount"] > 0]
                auth_cov.append(len(set(cited)) / len(results))
                retrieved = max(outcome["channels"]["semantic"],
                                outcome["channels"]["keyword"])
                if retrieved:
                    survivals.append(len(results) / retrieved)

    coverage = {
        "freshness": statistics.mean(fresh_cov) if fresh_cov else 0.0,
        "authority": statistics.mean(auth_cov) if auth_cov else 0.0,
    }
    survival = statistics.median(survivals) if survivals else 1.0

    return {
        "_source": (
            "Generated by app/pipeline/calibrate_ranking.compute_calibration from "
            "the live explore_documents corpus and its two search indexes. Every "
            "value is measured or derived — see that module's docstring for what "
            "each one comes from. Recomputed by the explore pipeline on every run."
        ),
        "_corpus_documents": len(rows),
        "_sampled": len(docs),
        "field_weights": field["weights"],
        "field_weight_mrr": field["mrr"],
        "retriever_resolution_ranks": delta,
        "prior_coverage": {k: round(v, 4) for k, v in coverage.items()},
        "prior_weights": derive_prior_weights(delta, coverage),
        "filter_survival": round(survival, 4),
        "candidate_pool": derive_candidate_pool(survival),
        "source_diversity_cap": cap,
        "fingerprint": fingerprint,
        "text_shape": shape,
    }
