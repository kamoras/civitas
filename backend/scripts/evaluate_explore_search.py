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

Nine query styles are probed, because the whole argument for a hybrid
engine is that different query shapes fail on different channels:

  title       the document's title words in order — the easy case
  paraphrase  the body's eight most frequent content words, title words
              removed. Despite the name, a bag of the document's own words,
              not a rewording: an exact-term query, and BM25's home ground
  identifier  serial numbers and citations lifted from the document's
              text (executive order numbers, RINs, FR citations, docket
              ids) — the case dense retrieval cannot do at all. Often a
              citation *of another document*, so the target is one of
              several right answers and R@1 understates success
  frcite      the document's OWN Federal Register citation ("91 FR 48248"),
  frdoc       and its own FR document number ("2026-15603"), from the
              identifiers it is known by (document_authority.
              declared_identifiers): what a researcher holding a reference
              types, with exactly one right answer. Measured here only
              (`build_own_identifier_probes`), like passage
  docket      the Regulations.gov docket its comment link names
              ("IRS-2026-1090"); several documents of one rulemaking share
              it, so R@1 understates
  crcite      a floor speech's Congressional Record citation ("172 Cong.
              Rec. H5836"), by the page its granule starts on; every
              speech in that granule answers it
  rare        the document's least common terms measured against the rest
              of the corpus — the long tail, where IDF earns its keep
  passage     one sentence of the body, verbatim, from anywhere in it —
              whether a specific passage is reachable, deep text included.
              Measured here only (`build_passage_probes`), not in the
              nightly field-weight fit, which reads `build_probes`

Every style is built from the document's own words, so the protocol
measures lexical findability and favours the keyword channel by
construction: semantic below keyword on ALL is expected, and identifier
near zero is a property of encoders, not a defect. Compare the semantic
channel with itself across a change to the index; synonymy, the case it
exists for, needs real queries.

Then identifiers the corpus does NOT hold (executive orders numbered just
before the oldest held, document numbers from a year before any held):
how many got documents back (none is right) and how many were told the
identifier isn't held, with where it is.

Reported per configuration: MRR (mean reciprocal rank of the target),
Recall@1 / @5 / @20, and how often the target was missed entirely.

**Through /search.** Since Explore's search page became the Documents
filter of `/search`, the results page ranks these documents together with
members, bills and every other kind (app/site_search.fuse). That list is
measured, on these document probes and on site search's own known items,
by scripts/calibrate_search_fusion.py, which reuses `build_probes` and
`build_own_identifier_probes` from here.

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

from app.pipeline.analyze.document_authority import (  # noqa: E402
    congressional_record_identifiers,
    declared_identifiers,
    docket_identifiers,
)
from app.pipeline.lexical_index import search_lexical  # noqa: E402
from app.pipeline.vector_store import search_explore_documents  # noqa: E402
from app.services.explore_search import hybrid_search  # noqa: E402

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
    "president", "secretary", "department", "office", "must",
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
        # In title order, not a set's: a set iterates in an order that
        # changes with every process's hash seed, so the same --seed built
        # different title and rare queries run to run, and two runs of an
        # unchanged engine disagreed by up to 0.04 MRR on those styles.
        ordered_title = list(dict.fromkeys(_terms(doc["title"])))
        title_terms = set(ordered_title)

        if len(title_terms) >= 2:
            probes.append({
                "style": "title", "doc_id": doc["id"],
                "query": " ".join(ordered_title[:8]),
            })

        body_terms = [t for t in _terms(body) if t not in title_terms]
        if len(set(body_terms)) >= 5:
            common = [t for t, _ in collections.Counter(body_terms).most_common(8)]
            probes.append({
                "style": "paraphrase", "doc_id": doc["id"],
                "query": " ".join(common),
            })

            # Rarest terms by inverse document frequency across this corpus.
            # Ties (common: many terms occur once) broken by the term
            # itself, for the same reason as the title order above.
            scored = sorted(
                set(body_terms),
                key=lambda t: (-math.log(total / max(corpus_df.get(t, 1), 1)), t),
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


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
PASSAGE_WORDS = (12, 40)  # a full sentence, not a heading or a citation block


def build_passage_probes(docs: list[dict]) -> list[dict]:
    """One verbatim body sentence per document, the middle one of those
    PASSAGE_WORDS long, from the whole body (not the first 6,000
    characters the other styles read): deep text is where the embedding
    window used to be truncated, and a probe that never looks there can't
    see it."""
    probes: list[dict] = []
    for doc in docs:
        sentences = [
            s for s in (p.strip() for p in _SENTENCE_BREAK.split(doc["body"] or ""))
            if PASSAGE_WORDS[0] <= len(s.split()) <= PASSAGE_WORDS[1]
        ]
        if sentences:
            probes.append({
                "style": "passage", "doc_id": doc["id"],
                "query": sentences[len(sentences) // 2],
            })
    return probes


def build_own_identifier_probes(docs: list[dict]) -> list[dict]:
    """The document's own FR citation and FR document number, typed the way
    the Federal Register prints them. Only documents that are known by one
    get a probe; the rest of the corpus (speeches, opinions) has none."""
    probes: list[dict] = []
    for doc in docs:
        own = declared_identifiers(
            doc.get("external_id"), doc.get("title"), doc.get("doc_type"),
            doc.get("identifiers"))
        for ident in sorted(own):
            namespace, _, value = ident.partition(":")
            if namespace == "fr":
                volume, _, page = value.partition("-")
                probes.append({"style": "frcite", "doc_id": doc["id"],
                               "query": f"{volume} FR {page}"})
            elif namespace == "frdoc":
                probes.append({"style": "frdoc", "doc_id": doc["id"],
                               "query": value})
        # The docket its comment link names, as Regulations.gov prints it
        # (one docket can hold several documents, so R@1 understates).
        dockets = sorted(i.split(":", 1)[1] for i in docket_identifiers(doc.get("comment_url")))
        if dockets:
            probes.append({"style": "docket", "doc_id": doc["id"], "query": min(dockets, key=len)})
        # A floor speech's Record citation, "172 Cong. Rec. H5836": every
        # speech in a granule starting on that page answers it.
        for ident in sorted(congressional_record_identifiers(doc.get("external_id"))):
            namespace, _, value = ident.partition(":")
            if namespace == "crec":
                volume, _, page = value.partition("-")
                probes.append({"style": "crcite", "doc_id": doc["id"],
                               "query": f"{volume} Cong. Rec. {page}"})
    return probes


def not_held_probes(rows) -> list[str]:
    """Identifiers the corpus does not hold: the twenty executive orders
    numbered just before the oldest one held, and Federal Register document
    numbers from a year before every one held. Each should get no
    unrelated document back."""
    eos = sorted(
        int(i.split(":", 1)[1]) for r in rows
        for i in declared_identifiers(r.external_id, r.title, r.doc_type, r.identifiers) if i.startswith("eo:"))
    probes = [f"EO {n}" for n in range(eos[0] - 20, eos[0])] if eos else []
    years = sorted(int((r.external_id or "").rsplit("-", 2)[-2]) for r in rows
                   if (r.external_id or "").startswith("fr-") and (r.external_id or "").rsplit("-", 2)[-2].isdigit())
    if years:
        probes += [f"{years[0] - 1}-{n:05d}" for n in range(10123, 30123, 1000)]
    return probes


def measure_not_held(db, queries: list[str]) -> dict:
    """How many documents come back for identifiers not held (none is
    right), and how often the response says the identifier isn't held."""
    returned = noted = 0
    for q in queries:
        outcome = hybrid_search(db, q, limit=RANK_CUTOFF)
        returned += bool(outcome["results"])
        noted += bool(outcome.get("notHeld"))
    n = len(queries) or 1
    return {"n": len(queries), "with_results": returned / n, "noted": noted / n}


HOW_TO_READ = """
`routed` is how often the right group showed the target at all; `other` how
often the other group showed something for the same query (a surname that
is also a bill's title word, say). Both groups sit beside the document
results, so `other` costs a glance, not a wrong answer.
"""


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
      in relative influence and dropped ALL/hybrid from MRR 0.850 / R@1
      0.732 to 0.752 / 0.613 on an identical corpus.
"""


def _rank_of(results: list[int], doc_id: int) -> int | None:
    try:
        return results.index(doc_id) + 1
    except ValueError:
        return None


CONFIGS = ("semantic", "keyword", "fusion", "hybrid")


def measure(db, probes: list[dict], configs: tuple[str, ...] = CONFIGS) -> dict:
    """Run every probe through the requested configurations.

    Returns `{style: {config: [rank or None, ...]}}`, with an "ALL" style
    aggregating across the rest.

    Separated from `main` so the loop that actually produces the numbers is
    reachable from a test. It is the part with something to get wrong — one
    misread result key and every configuration reports a clean 100% miss,
    which looks exactly like "this channel is broken" rather than "the
    harness is". That failure mode is why this exists as a function.

    `configs` exists because three of the four channels embed the probe
    query, and a caller that reads only `keyword` pays for three
    sentence-transformer forward passes per probe to discard the result.
    Measured against the live 8,372-document corpus, the field-weight fit
    in `calibrate_ranking` ran 44,957 probe iterations: 15.5 hours running
    all four, 28 minutes running only the channel it reads — with
    identical output, since a channel whose ranks are never inspected
    cannot change the fit. That is what wedged the nightly explore
    pipeline past its 8-hour budget.
    """
    by_style: dict[str, dict[str, list]] = collections.defaultdict(
        lambda: collections.defaultdict(list))

    for probe in probes:
        query = probe["query"]
        results: dict[str, list[int]] = {}
        if "semantic" in configs:
            semantic = search_explore_documents(query, n_results=RANK_CUTOFF)
            results["semantic"] = [h["id"] for h in (semantic or [])]
        if "keyword" in configs:
            results["keyword"] = [
                h["id"] for h in search_lexical(db, query, limit=RANK_CUTOFF)
            ]
        if "fusion" in configs:
            results["fusion"] = [
                r["id"] for r in hybrid_search(
                    db, query, limit=RANK_CUTOFF,
                    include_priors=False)["results"]
            ]
        if "hybrid" in configs:
            results["hybrid"] = [
                r["id"] for r in hybrid_search(
                    db, query, limit=RANK_CUTOFF)["results"]
            ]
        for name in configs:
            rank = _rank_of(results[name], probe["doc_id"])
            by_style[probe["style"]][name].append(rank)
            by_style["ALL"][name].append(rank)

    return by_style


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

    db = SessionLocal()
    try:
        total = db.query(ExploreDocument).count()
        if total == 0:
            print("No explore documents indexed — run the explore pipeline first.")
            return 1

        random.seed(args.seed)
        rows = db.query(
            ExploreDocument.id, ExploreDocument.title, ExploreDocument.body,
            ExploreDocument.external_id, ExploreDocument.doc_type,
            ExploreDocument.identifiers, ExploreDocument.comment_url,
        ).order_by(ExploreDocument.id).all()
        sample = random.sample(rows, min(args.samples, len(rows)))

        # Document frequency over the sample, which is what the "rare" style
        # needs to tell a distinctive term from a common one. Sample-based,
        # so it tracks the corpus without a second full pass over every body.
        corpus_df: dict[str, int] = collections.Counter()
        for row in sample:
            for term in set(_terms(f"{row.title} {(row.body or '')[:6000]}")):
                corpus_df[term] += 1

        docs = [
            {"id": r.id, "title": r.title, "body": r.body,
             "external_id": r.external_id, "doc_type": r.doc_type,
             "identifiers": r.identifiers, "comment_url": r.comment_url}
            for r in sample
        ]
        probes = (build_probes(docs, corpus_df, len(sample))
                  + build_passage_probes(docs)
                  + build_own_identifier_probes(docs))
        print(f"Corpus: {total} documents | sampled {len(sample)} | "
              f"{len(probes)} probes\n")

        by_style = measure(db, probes)

        header = f"{'style':<12}{'config':<10}{'n':>6}{'MRR':>8}{'R@1':>8}{'R@5':>8}{'R@20':>8}{'missed':>9}"
        print(header)
        print("-" * len(header))
        for style in ("title", "paraphrase", "identifier", "frcite", "frdoc",
                      "docket", "crcite", "rare", "passage", "ALL"):
            if style not in by_style:
                continue
            for config in CONFIGS:
                stats = _summarise(by_style[style][config])
                print(f"{style:<12}{config:<10}{stats['n']:>6}"
                      f"{stats['mrr']:>8.3f}{stats['r@1']:>8.3f}"
                      f"{stats['r@5']:>8.3f}{stats['r@20']:>8.3f}"
                      f"{stats['missed']:>9.3f}")
            print()

        held_out = measure_not_held(db, not_held_probes(rows))
        print(f"not held    {held_out['n']} identifiers the corpus does not hold: "
              f"{held_out['with_results']:.3f} got documents back, "
              f"{held_out['noted']:.3f} were told it isn't held\n")

        print(HOW_TO_READ)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
