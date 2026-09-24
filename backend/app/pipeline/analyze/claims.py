"""Turn a cluster of articles into verified claims, never into prose.

The Action Center's generation step asks one 1.2B model to write a
title, a summary and 3-5 facts at once, from a 4,361-character prompt
carrying 16 separate "never / do not" clauses. Nearly every clause is a
fossilised bug — "never write 'reports say'", "double-check the
direction of every action", "never state 'found guilty' unless THAT
SPECIFIC PERSON", "never write X surpasses X" — and the module's own
comment concedes the approach does not work: "the local model doesn't
reliably follow prompt-only instructions".

Downstream of that sits a repair stack that exists only because
generation is unconstrained: string surgery on impossible vote tallies,
geographic-consistency patching, hallucinated-role stripping, a retry
loop, and a second LLM call to check the first — which fails OPEN, so
the platform's highest-severity error class is guarded by an unreliable
check that defaults to publishing.

This module removes the freedom instead of policing it. A model is
asked only to LOCATE an assertion in one article; `post_composer`
verifies both spans are verbatim AND that the source asserts one of the
other, then renders the sentence. The model still chooses what is
newsworthy — the judgement it is good at — and never chooses the words,
which is where it was failing.

`_build_actions_from_data` in action_center.py already made exactly this
move for the `actions` field ("Build action items from real data — no
LLM hallucinations"). This extends a pattern the codebase already
proved, to the fields that still generate prose.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from app.pipeline.analyze.post_composer import compose

logger = logging.getLogger(__name__)

# How many facts an issue carries. Matches what the old prompt asked for
# ("3-5 key factual bullet points"), so the reader-facing shape is
# unchanged — only how the text is obtained.
MAX_FACTS = 5

# A claim scoring below this fraction of the BEST claim's topic score is
# an outlier among its siblings, not a supporting fact. Relative rather
# than absolute so it needs no recalibration, and generous on purpose:
# the cost of keeping a marginal claim is one weaker fact, while the
# cost of dropping a good one is — measured — an entire issue not
# published.
OUTLIER_FRACTION = 0.5


@dataclass(frozen=True)
class Claim:
    """One assertion, as the source made it, with its provenance."""
    text: str          # "actor predicate." — rendered, never paraphrased
    source_name: str
    source_url: str


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in "".join(
        c.lower() if (c.isalnum() or c.isspace()) else " " for c in text
    ).split() if len(w) > 2)


def dedupe_claims(claims: list[Claim]) -> list[Claim]:
    """Drop claims that say nothing the kept ones do not.

    A cluster is by construction several outlets covering ONE event, so
    near-identical claims are the normal case, not the exception. The
    rule is containment rather than a similarity threshold: a claim
    whose words are a subset of one already kept adds nothing, and the
    longer of two nested claims is the more specific. No tunable, so
    nothing here can drift or need recalibrating.
    """
    kept: list[Claim] = []
    for claim in sorted(claims, key=lambda c: len(c.text), reverse=True):
        words = _tokens(claim.text)
        if not words:
            continue
        if any(words <= _tokens(k.text) for k in kept):
            continue
        kept.append(claim)
    return kept


def extract_claims(
    articles: list,
    ask: Callable[[str], dict | None],
) -> list[Claim]:
    """Verified claims from a cluster, best-supported first.

    `ask` takes the article text and returns the model's located spans —
    injected so the selection logic is testable without a model, and so
    a caller can batch or cache however it likes.

    Order is the cluster's own: articles arrive ranked, and an earlier
    article is a better-sourced account of the same event. Nothing is
    re-ranked here, which keeps the one ordering decision in the caller
    that already owns ranking.
    """
    claims: list[Claim] = []
    for article in articles:
        source = f"{getattr(article, 'title', '')}\n{getattr(article, 'summary', '') or ''}".strip()
        if not source:
            continue
        try:
            located = ask(source) or {}
        except Exception:
            logger.exception("Claim extraction failed for %s", getattr(article, "url", "?"))
            continue
        text = compose(
            str(located.get("actor") or ""),
            str(located.get("predicate") or ""),
            source,
        )
        if not text:
            # Normal and frequent: an article with no attributable
            # assertion yields nothing rather than a sentence about
            # itself. Measured on real coverage, most articles do this.
            continue
        claims.append(Claim(
            text=text,
            source_name=getattr(article, "source_name", "") or "",
            source_url=getattr(article, "url", "") or "",
        ))
    return dedupe_claims(claims)


def on_topic(claims: list[Claim], articles: list) -> list[Claim]:
    """Drop claims that are not about what the cluster is about.

    Extraction is faithful to its article, which means it is only as
    good as the clustering: measured on a live run, the cluster titled
    "Does the UN have a future?" contained a BBC housing story and duly
    produced "Maricarmen was unable to pay the rent set by the firm
    which recently bought it." A true sentence, correctly attributed,
    and nothing to do with the issue.

    The bar is relative to the OTHER CLAIMS, not to the articles.

    The first version compared each claim against the least on-topic
    ARTICLE, which sounded principled and was measurably wrong: a claim
    is one sentence and an article is a title plus a summary, so short
    text scores systematically lower against a long-text centroid. The
    two sides were never comparable. Measured on live clusters, that bar
    landed at 0.637 and discarded a perfectly good claim scoring 0.601 —
    and with the substance gate needing two claims, whole issues
    vanished. The Action Center published ZERO issues for an hour
    because of it.

    Comparing claims to claims keeps the property that mattered — no
    typed constant, nothing to recalibrate when the embedding model
    changes — while comparing like with like. A claim is dropped when it
    is a clear outlier among its siblings, which is what the
    "Maricarmen was unable to pay the rent" case actually was: 0.146
    against a sibling at 0.564.
    """
    texts = [f"{getattr(a, 'title', '')} {getattr(a, 'summary', '') or ''}".strip() for a in articles]
    texts = [t for t in texts if t]
    if not claims or len(texts) < 2:
        return claims
    try:
        import numpy as np

        from app.pipeline.vector_store import encode_normalized, get_similarity_model

        model = get_similarity_model()
        article_vecs = encode_normalized(model, texts)
        centroid = article_vecs.mean(axis=0)
        norm = np.linalg.norm(centroid) or 1.0
        centroid = centroid / norm
        claim_vecs = encode_normalized(model, [c.text for c in claims])
        scores = claim_vecs @ centroid
        # Half the best claim's score. Relative, so it scales with
        # however tightly this particular cluster embeds, and it can
        # only ever drop a claim that is far worse than one we are
        # already publishing. A lone claim is never its own outlier.
        floor = float(max(scores)) * OUTLIER_FRACTION
    except Exception:
        logger.exception("Claim topic check failed — keeping every claim")
        return claims

    kept = [c for c, s in zip(claims, scores) if float(s) >= floor]
    if len(kept) != len(claims):
        logger.info(
            "Dropped %d off-topic claim(s) below the cluster's own floor %.3f",
            len(claims) - len(kept), floor,
        )
    return kept


def build_facts(claims: list[Claim], limit: int = MAX_FACTS) -> tuple[list[str], list[str]]:
    """(facts, aligned source names).

    Two aligned lists rather than a list of objects so `facts` keeps its
    `list[str]` shape — `previous_facts`, `bsky_posted_facts`, `newFacts`
    and `_issue_signature` all compare fact strings and keep working
    untouched.
    """
    chosen = claims[:limit]
    return [c.text for c in chosen], [c.source_name for c in chosen]


def build_lede(claims: list[Claim]) -> str:
    """The issue summary: the single best-supported claim, verbatim.

    Deliberately not a synthesis. A synthesis is where the connecting
    language lives, and connecting language is where role reversal and
    editorialising live with it — the summary is exactly the field that
    published "E. Jean Carroll was found guilty" and "Iran War Ends
    Quickly to Lower Prices".
    """
    return claims[0].text if claims else ""
