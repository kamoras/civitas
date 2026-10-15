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

Seven query styles are probed, because the whole argument for a hybrid
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

Reported per configuration: MRR (mean reciprocal rank of the target),
Recall@1 / @5 / @20, and how often the target was missed entirely.

**Typed groups (members and bills).** Explore also offers the sitting
members and the bills a search names, as their own groups beside the
documents (services/explore_matches.py), never fused into the document
ranking. They are measured the same known-item way, from the records
themselves: a member's surname, full name and a one-letter-transposed
surname; a bill's number as the Record prints it, its short title (or
title) in order, and two of its title words out of order. Reported: each
group's MRR and R@1/R@5 over what it shows (GROUP_LIMIT), and routing —
how often the right group shows the target at all, and how often the
other group shows something too (`--skip-documents` runs only this part,
with no embedding model). The document probes are also run through the
groups, to report how often a document search gets a group beside it.

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

from app.models import Representative, Senator  # noqa: E402
from app.pipeline.analyze.document_authority import declared_identifiers  # noqa: E402
from app.pipeline.lexical_index import search_lexical  # noqa: E402
from app.pipeline.vector_store import search_explore_documents  # noqa: E402
from app.services.bill_service import get_bills_in_flight  # noqa: E402
from app.services.congress_service import bill_label  # noqa: E402
from app.services.explore_matches import GROUP_LIMIT, bill_matches, member_matches  # noqa: E402
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
    return probes


def _transposed(word: str, rng: random.Random) -> str:
    """`word` with two adjacent letters swapped (not the first): the
    commonest typing slip, for the misspelt-surname probe."""
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def build_member_probes(members: list[dict], rng: random.Random) -> list[dict]:
    """Known-item member queries: surname, full name, a typo'd surname."""
    probes = []
    for m in members:
        surname = m["name"].split()[-1].lower()
        probes += [
            {"group": "members", "style": "surname", "target": m["id"], "query": surname},
            {"group": "members", "style": "full name", "target": m["id"], "query": m["name"]},
            {"group": "members", "style": "typo", "target": m["id"], "query": _transposed(surname, rng)},
        ]
    return probes


def build_bill_probes(bills: list[dict], rng: random.Random) -> list[dict]:
    """Known-item bill queries: its number as printed, its short title (or
    title) in order, two of its title words out of order."""
    probes = []
    for b in bills:
        probes.append({"group": "bills", "style": "number", "target": b["billId"], "query": b["label"]})
        name = b["shortTitle"] or b["title"]
        probes.append({"group": "bills", "style": "name", "target": b["billId"],
                       "query": " ".join(name.split()[:8])})
        words = list(dict.fromkeys(_terms(name)))
        if len(words) >= 3:
            pair = rng.sample(words, 2)
            probes.append({"group": "bills", "style": "words", "target": b["billId"],
                           "query": " ".join(pair)})
    return probes


def measure_groups(db, probes: list[dict]) -> dict:
    """{(group, style): {"ranks": [...], "other": [...]}}: the target's rank
    in its own group's shown list (None when not shown), and whether the
    other group showed anything for the same query."""
    out: dict = collections.defaultdict(lambda: {"ranks": [], "other": []})
    for probe in probes:
        _, members = member_matches(db, probe["query"])
        _, bills = bill_matches(db, probe["query"])
        own, other = (
            ([m["id"] for m in members], bills) if probe["group"] == "members"
            else ([b["billId"] for b in bills], members)
        )
        cell = out[(probe["group"], probe["style"])]
        cell["ranks"].append(_rank_of(own, probe["target"]))
        cell["other"].append(bool(other))
    return out


def group_noise(db, probes: list[dict]) -> dict:
    """How often a document probe's query gets each group beside the results."""
    shown = {"members": 0, "bills": 0}
    for probe in probes:
        shown["members"] += bool(member_matches(db, probe["query"])[0])
        shown["bills"] += bool(bill_matches(db, probe["query"])[0])
    n = len(probes) or 1
    return {k: v / n for k, v in shown.items()}


def _print_groups(db, member_rows, bill_rows, doc_probes, rng) -> None:
    probes = build_member_probes(member_rows, rng) + build_bill_probes(bill_rows, rng)
    by = measure_groups(db, probes)
    header = (f"{'group':<9}{'style':<11}{'n':>5}{'MRR':>8}{'R@1':>8}"
              f"{f'R@{GROUP_LIMIT}':>8}{'routed':>8}{'other':>8}")
    print(f"Typed groups (shown: {GROUP_LIMIT} each)\n")
    print(header)
    print("-" * len(header))
    for (group, style), cell in sorted(by.items()):
        stats = _summarise(cell["ranks"])
        routed = 1 - stats["missed"]
        other = sum(cell["other"]) / (len(cell["other"]) or 1)
        print(f"{group:<9}{style:<11}{stats['n']:>5}{stats['mrr']:>8.3f}{stats['r@1']:>8.3f}"
              f"{stats['r@5']:>8.3f}{routed:>8.3f}{other:>8.3f}")
    if doc_probes:
        noise = group_noise(db, doc_probes)
        print(f"\nDocument probes with a group beside the results: members {noise['members']:.3f}, "
              f"bills {noise['bills']:.3f} (of {len(doc_probes)})")
    print(GROUPS_HOW_TO_READ)


GROUPS_HOW_TO_READ = """
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
    parser.add_argument("--skip-documents", action="store_true",
                        help="measure only the typed groups (no embedding model)")
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
            ExploreDocument.identifiers,
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
             "identifiers": r.identifiers}
            for r in sample
        ]
        probes = (build_probes(docs, corpus_df, len(sample))
                  + build_passage_probes(docs)
                  + build_own_identifier_probes(docs))
        print(f"Corpus: {total} documents | sampled {len(sample)} | "
              f"{len(probes)} probes\n")

        rng = random.Random(args.seed)
        member_rows = [
            {"id": pid, "name": name}
            for model in (Senator, Representative)
            for pid, name in db.query(model.id, model.name).filter(model.is_current == True)  # noqa: E712
        ]
        member_rows = rng.sample(sorted(member_rows, key=lambda m: m["id"]), min(args.samples, len(member_rows)))
        bills = get_bills_in_flight(db, per_page=100000).bills
        bill_rows = [
            {"billId": b.bill_id, "label": bill_label(b.bill_id) or b.bill_id,
             "title": b.title, "shortTitle": b.short_title}
            for b in rng.sample(sorted(bills, key=lambda b: b.bill_id), min(args.samples, len(bills)))
        ]
        if args.skip_documents:
            _print_groups(db, member_rows, bill_rows, probes, rng)
            return 0

        by_style = measure(db, probes)

        header = f"{'style':<12}{'config':<10}{'n':>6}{'MRR':>8}{'R@1':>8}{'R@5':>8}{'R@20':>8}{'missed':>9}"
        print(header)
        print("-" * len(header))
        for style in ("title", "paraphrase", "identifier", "frcite", "frdoc",
                      "rare", "passage", "ALL"):
            if style not in by_style:
                continue
            for config in CONFIGS:
                stats = _summarise(by_style[style][config])
                print(f"{style:<12}{config:<10}{stats['n']:>6}"
                      f"{stats['mrr']:>8.3f}{stats['r@1']:>8.3f}"
                      f"{stats['r@5']:>8.3f}{stats['r@20']:>8.3f}"
                      f"{stats['missed']:>9.3f}")
            print()

        print(HOW_TO_READ)
        _print_groups(db, member_rows, bill_rows, probes, rng)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
