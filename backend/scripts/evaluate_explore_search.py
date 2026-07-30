"""Measure explore search quality: semantic vs keyword vs hybrid.

The ranking weights in `config_definitions` ("Explore search ranking") are
starting points, not measurements. This is the instrument that turns them
into measurements — run it against the live index, change a weight, run it
again. Per the repo's calibration discipline, that is the only acceptable
way to move them; "this looks better" is how a ranking function
accumulates changes nobody can defend.

**Relevance judgments are derived, not hand-labelled.** This is the
known-item retrieval protocol: take a document out of the corpus, build a
query that a person looking for *that* document plausibly would have
typed, and measure how far down the results the document actually
appears. The document is the only correct answer by construction, so no
human has to label anything and the judgments cannot drift as the corpus
changes. It measures exactly one thing — can the engine find a document
someone is looking for — and deliberately says nothing about whether a
broad topical query returns a *good* set. That question needs real
labels, and a synthetic harness that pretended otherwise would be worse
than no harness.

Four query styles are probed, because the whole argument for a hybrid
engine is that different query shapes fail on different channels:

  title       the document's title, near-verbatim — the easy case
  paraphrase  content words from the body, title words removed — the case
              dense retrieval should win and BM25 should struggle
  identifier  serial numbers and citations lifted from the document
              (executive order numbers, RINs, FR citations, docket ids) —
              the case dense retrieval cannot do at all, and the reason
              the keyword channel exists
  rare        the document's least common terms measured against the rest
              of the corpus — the long tail, where IDF earns its keep

Reported per configuration: MRR (mean reciprocal rank of the target),
Recall@1 / @5 / @20, and how often the target was missed entirely.

Run against the deployed database:
    cd backend && .venv/bin/python scripts/evaluate_explore_search.py
    cd backend && .venv/bin/python scripts/evaluate_explore_search.py --samples 300
"""

import argparse
import collections
import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_SAMPLES = 150
RANK_CUTOFF = 50

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']{3,}")
_IDENTIFIER_RE = re.compile(
    r"(?:Executive Order\s+\d{4,5}"
    r"|E\.\s?O\.\s*\d{4,5}"
    r"|\d{2,3}\s+FR\s+\d{1,6}"
    r"|RIN\s+\d{4}-[A-Z]{2}\d{2}"
    r"|[A-Z]{2,}-[A-Z0-9]+-\d{4}-\d{4})"
)

# Words too generic to identify a document. Not a classification rule —
# these are the fixed boilerplate of the Federal Register's own house
# style, and a query made of them names every document equally.
_BOILERPLATE = {
    "shall", "such", "that", "this", "there", "these", "those", "with",
    "which", "under", "section", "subsection", "paragraph", "chapter",
    "part", "parts", "federal", "register", "agency", "agencies", "rule",
    "rules", "final", "proposed", "notice", "notices", "public", "comment",
    "comments", "document", "documents", "united", "states", "government",
    "president", "secretary", "department", "office", "shall", "must",
    "will", "would", "been", "have", "from", "into", "upon", "their",
    "other", "also", "than", "when", "where", "date", "dates", "effective",
}


def _terms(text: str) -> list[str]:
    return [
        word.lower() for word in _WORD_RE.findall(text or "")
        if word.lower() not in _BOILERPLATE
    ]


def build_probes(docs: list[dict], corpus_df: dict[str, int], total: int) -> list[dict]:
    """Build known-item queries for one document, one per style it supports."""
    probes: list[dict] = []
    for doc in docs:
        body = (doc["body"] or "")[:6000]
        title_terms = set(_terms(doc["title"]))

        if len(title_terms) >= 2:
            probes.append({
                "style": "title", "doc_id": doc["id"],
                "query": " ".join(list(title_terms)[:8]),
            })

        body_terms = [t for t in _terms(body) if t not in title_terms]
        if len(set(body_terms)) >= 5:
            common = [t for t, _ in collections.Counter(body_terms).most_common(8)]
            probes.append({
                "style": "paraphrase", "doc_id": doc["id"],
                "query": " ".join(common),
            })

            # Rarest terms by inverse document frequency across this corpus.
            scored = sorted(
                set(body_terms),
                key=lambda t: math.log(total / max(corpus_df.get(t, 1), 1)),
                reverse=True,
            )
            probes.append({
                "style": "rare", "doc_id": doc["id"],
                "query": " ".join(scored[:4]),
            })

        identifiers = _IDENTIFIER_RE.findall(f"{doc['title']} {body}")
        if identifiers:
            probes.append({
                "style": "identifier", "doc_id": doc["id"],
                "query": identifiers[0],
            })

    return probes


HOW_TO_READ = """
How to read this
----------------
`fusion` is the two retrieval channels combined with the priors switched
off. `hybrid` is what production serves. Compare them deliberately:

  fusion vs semantic/keyword
      The question this measurement can answer. If `fusion` does not beat
      both channels on ALL, the retrieval side is mistuned — or one channel
      is broken and the other is carrying it.

  hybrid vs fusion
      The question this measurement CANNOT answer, and the trap to avoid.
      Known-item retrieval defines exactly one correct document per query,
      and the retrieval channels have usually already put it first. From
      there, any reordering by recency or citation authority can only move
      it down, so `hybrid` scores at or below `fusion` *by construction* —
      even when the priors are doing precisely their job. Do NOT read that
      gap as evidence to reduce or remove them.

      The priors exist for the case this protocol cannot produce: a broad
      query where many documents are genuinely relevant and the question is
      which of them to show first. Judging that needs relevance labels over
      real queries — human judgements or click data — not a synthetic
      known-item probe.

  What the hybrid column IS good for
      Catching a prior that has become disproportionate rather than merely
      present. A few points below `fusion` is the expected cost of ranking
      by more than relevance; a collapse is a bug. That is how the
      live-channel scaling in explore_search.hybrid_search was found: with
      one retrieval channel returning nothing, fixed prior weights doubled
      in relative influence and dropped ALL/hybrid from 0.85 to 0.76 MRR.
"""


def _rank_of(results: list[int], doc_id: int) -> int | None:
    try:
        return results.index(doc_id) + 1
    except ValueError:
        return None


def _summarise(ranks: list[int | None]) -> dict:
    found = [r for r in ranks if r is not None]
    n = len(ranks) or 1
    return {
        "n": len(ranks),
        "mrr": sum(1.0 / r for r in found) / n,
        "r@1": sum(1 for r in found if r <= 1) / n,
        "r@5": sum(1 for r in found if r <= 5) / n,
        "r@20": sum(1 for r in found if r <= 20) / n,
        "missed": sum(1 for r in ranks if r is None) / n,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                        help="documents to sample as known items")
    parser.add_argument("--seed", type=int, default=20260730,
                        help="sampling seed (fixed so runs are comparable)")
    args = parser.parse_args()

    from app.database import SessionLocal
    from app.models import ExploreDocument
    from app.pipeline.lexical_index import search_lexical
    from app.pipeline.vector_store import search_explore_documents
    from app.services.explore_search import hybrid_search

    db = SessionLocal()
    try:
        total = db.query(ExploreDocument).count()
        if total == 0:
            print("No explore documents indexed — run the explore pipeline first.")
            return 1

        random.seed(args.seed)
        rows = db.query(
            ExploreDocument.id, ExploreDocument.title, ExploreDocument.body,
        ).all()
        sample = random.sample(rows, min(args.samples, len(rows)))

        # Document frequency over the sample, which is what the "rare" style
        # needs to tell a distinctive term from a common one. Sample-based,
        # so it tracks the corpus without a second full pass over every body.
        corpus_df: dict[str, int] = collections.Counter()
        for row in sample:
            for term in set(_terms(f"{row.title} {(row.body or '')[:6000]}")):
                corpus_df[term] += 1

        probes = build_probes(
            [{"id": r.id, "title": r.title, "body": r.body} for r in sample],
            corpus_df, len(sample),
        )
        print(f"Corpus: {total} documents | sampled {len(sample)} | "
              f"{len(probes)} probes\n")

        by_style: dict[str, dict[str, list]] = collections.defaultdict(
            lambda: collections.defaultdict(list))

        for probe in probes:
            semantic = search_explore_documents(probe["query"], n_results=RANK_CUTOFF)
            semantic_ids = [h["id"] for h in (semantic or [])]
            keyword_ids = [
                h["id"] for h in search_lexical(db, probe["query"], limit=RANK_CUTOFF)
            ]
            fusion_ids = [
                r["id"] for r in hybrid_search(
                    db, probe["query"], limit=RANK_CUTOFF,
                    include_priors=False)["results"]
            ]
            hybrid_ids = [
                r["id"] for r in hybrid_search(
                    db, probe["query"], limit=RANK_CUTOFF)["results"]
            ]

            for name, ids in (
                ("semantic", semantic_ids),
                ("keyword", keyword_ids),
                ("fusion", fusion_ids),
                ("hybrid", hybrid_ids),
            ):
                by_style[probe["style"]][name].append(_rank_of(ids, probe["doc_id"]))
                by_style["ALL"][name].append(_rank_of(ids, probe["doc_id"]))

        header = f"{'style':<12}{'config':<10}{'n':>6}{'MRR':>8}{'R@1':>8}{'R@5':>8}{'R@20':>8}{'missed':>9}"
        print(header)
        print("-" * len(header))
        for style in ("title", "paraphrase", "identifier", "rare", "ALL"):
            if style not in by_style:
                continue
            for config in ("semantic", "keyword", "fusion", "hybrid"):
                stats = _summarise(by_style[style][config])
                print(f"{style:<12}{config:<10}{stats['n']:>6}"
                      f"{stats['mrr']:>8.3f}{stats['r@1']:>8.3f}"
                      f"{stats['r@5']:>8.3f}{stats['r@20']:>8.3f}"
                      f"{stats['missed']:>9.3f}")
            print()

        print(HOW_TO_READ)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
