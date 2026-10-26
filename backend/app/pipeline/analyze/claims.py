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

import json
import logging
import pathlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

from app import calibration_drift
from app.database import SessionLocal
from app.models import LlmGenerationSample
from app.pipeline.analyze.post_composer import (
    _CLAIM_SOURCE_END, _CLAIM_SOURCE_START, CLAIM_TASK, compose, headline_source,
)
from app.pipeline import vector_store
from app.pipeline.cache import api_cache_get
from app.time_utils import utcnow

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
    # Who did or said it: the actor span compose() verified, as the model
    # located it. Restatement is decided from it and the text together
    # (distinct_claims).
    actor: str = ""


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
    return dedupe_claims(_extract(
        articles, ask,
        lambda a: headline_source(getattr(a, "title", ""), getattr(a, "summary", "")),
    ))


def extract_body_claims(
    articles: list,
    ask: Callable[[str], dict | None],
) -> list[Claim]:
    """A second claim per article, located in its summary alone.

    Asked about headline and summary together, the model points at the
    headline nearly every time (all 14 claims in a 40-article live sample,
    2026-09-27), so an article's body never contributes. The full story
    is what that body is for; hiding the headline is how it is reached,
    with the same prompt and the same verification.
    """
    return _extract(articles, ask, lambda a: (getattr(a, "summary", "") or "").strip())


def _extract(articles: list, ask: Callable[[str], dict | None], source_of) -> list[Claim]:
    claims: list[Claim] = []
    for article in articles:
        source = source_of(article)
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
            actor=str(located.get("actor") or "").strip().strip(",;:"),
        ))
    return claims


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
        model = vector_store.get_similarity_model()
        article_vecs = vector_store.encode_normalized(model, texts)
        centroid = article_vecs.mean(axis=0)
        norm = np.linalg.norm(centroid) or 1.0
        centroid = centroid / norm
        claim_vecs = vector_store.encode_normalized(model, [c.text for c in claims])
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


# Two claims that report one fact are one fact. dedupe_claims catches a
# claim whose words are a subset of another's; a restatement in other words
# ("Federal agents raid her home" / "Federal agents search her D.C. home", a
# headline and its own lede) passed it, and the two-claim gate counted it
# twice: replayed on 2026-10-11..15, a two-story briefing's two lines about
# its second story made a two-claim issue. distinct_claims drops a claim
# whose similarity-model cosine with an earlier one reaches a threshold the
# Action Center refits once a UTC day from its own issues and feed articles
# (action_center.fit_restatement_cut: above a high quantile of the pairs
# the data calls two facts, adopted only under precision_rule.accept
# against the cut in use, held out on blocks of days), stored in
# api_cache; app/data/claim_restatement_calibration.json, the same fit on a
# production snapshot (scripts/calibrate_action_cuts.py), until the first
# refit. A false call drops a real second fact and with it, often, the
# issue, so the cut is precision-first. That leaves some rewordings of one
# fact below it (measured 2026-10-25 on the replayed clusters: one fact in
# two outlets' words at 0.62 to 0.86, two facts at 0.51 to 0.84), which the
# two-claim gate still counts twice.
#
# Two claims about the same actor (same_actor) are held to their own cut,
# `threshold_same_actor`, when the refit has one: fitted the same way over
# the two-fact pairs whose actors match, which needs MIN_HARD_NEGATIVES of
# them, and adopted only under precision_rule.accept against the rule in
# use (action_center.fit_restatement_cut). Without one, every pair is held
# to `threshold`.
_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / "data"
RESTATEMENT_CALIBRATION_PATH = _DATA_DIR / "claim_restatement_calibration.json"
_RESTATEMENT_TIER, _RESTATEMENT_KEY = "action-cuts", "restatement"
_restatement_calibration_cache: tuple[dict, datetime] | None = None


def set_restatement_calibration(fitted: dict) -> None:
    """Use a fresh refit in this process at once (action_center.refit_cuts)."""
    global _restatement_calibration_cache
    _restatement_calibration_cache = (fitted, utcnow())


def restatement_calibration() -> dict:
    """The restatement cut in use ({"threshold": ...}): the day's refit,
    re-read hourly, else the bundled one."""
    global _restatement_calibration_cache
    if _restatement_calibration_cache and utcnow() - _restatement_calibration_cache[1] < timedelta(hours=1):
        return _restatement_calibration_cache[0]
    stored = None
    try:
        with SessionLocal() as session:
            stored = api_cache_get(session, _RESTATEMENT_TIER, _RESTATEMENT_KEY, max_age_hours=24 * 400)
    except Exception:
        logger.warning("Stored restatement cut unreadable; using the bundled one", exc_info=True)
    value = stored or json.loads(RESTATEMENT_CALIBRATION_PATH.read_text())
    _restatement_calibration_cache = (value, utcnow())
    return value


# Words two lines must share in a row to be one sentence quoted twice: the
# restatement fit's one-fact label (action_center.restatement_pairs). Not
# a rule distinct_claims applies: headlines share five-word templates
# ("watch live: Trump rallies in", "as White House press secretary")
# across different facts.
SAME_SENTENCE_RUN = 5
_RUN_WORD_RE = re.compile(r"[a-z0-9']+")
# A figure a line states: digits, with a unit word or sign it carries. A
# four-digit year is a date, not a figure.
_FIGURE_RE = re.compile(r"(?<![\w.])\$?\d[\d,.]*(?:\s?(?:million|billion|trillion|percent|%))?", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:1[7-9]|20)\d\d")


def shared_run(a: str, b: str) -> int:
    """The longest run of words two lines share, verbatim."""
    x, y = _RUN_WORD_RE.findall(a.lower()), _RUN_WORD_RE.findall(b.lower())
    return SequenceMatcher(None, x, y, autojunk=False).find_longest_match(0, len(x), 0, len(y)).size


def stated_figures(text: str) -> frozenset[str]:
    """The figures a line states ("$18 million", "250,000"), years left out."""
    out = set()
    for m in _FIGURE_RE.finditer(text or ""):
        figure = m.group(0).rstrip(".,").lower().replace("$", "").replace(",", "")
        if not _YEAR_RE.fullmatch(figure):
            out.add(figure)
    return frozenset(out)


_ACTOR_WORD_RE = re.compile(r"[a-z0-9]+")
_ARTICLES = frozenset({"a", "an", "the"})


def same_actor(a: str | None, b: str | None) -> bool:
    """Whether two actor spans name one party: one's words (articles aside)
    are all among the other's ("Trump" and "President Trump", "Rep. Jane
    Doe" and "U.S. Rep. Jane Doe"). A name match, not a classification:
    spans that name one party differently ("DOJ", "Justice Department")
    read as two, which holds the pair to the cut every pair is held to."""
    wa = frozenset(_ACTOR_WORD_RE.findall((a or "").lower())) - _ARTICLES
    wb = frozenset(_ACTOR_WORD_RE.findall((b or "").lower())) - _ARTICLES
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def restates(score: float, actor_a: str | None, actor_b: str | None, calibration: dict) -> bool:
    """Whether two claims at cosine SCORE are one fact under CALIBRATION
    (restatement_calibration's shape)."""
    same_cut = calibration.get("threshold_same_actor")
    cut = same_cut if same_cut is not None and same_actor(actor_a, actor_b) else calibration["threshold"]
    return score >= cut


def recorded_claims(db, since: datetime) -> list[tuple]:
    """(source, date, claim, actor) for each claim the claim calls recorded
    since SINCE located and compose() verifies, at its first recording: the
    restatement fit's lines that carry their actor."""
    rows = (db.query(LlmGenerationSample.input_text, LlmGenerationSample.output_json, LlmGenerationSample.created_at)
            .filter(LlmGenerationSample.task == CLAIM_TASK, LlmGenerationSample.passed.is_(True),
                    LlmGenerationSample.created_at >= since)
            .order_by(LlmGenerationSample.id))
    out, seen = [], set()
    for prompt, answer, at in rows:
        source = prompt.split(_CLAIM_SOURCE_START, 1)[-1].rsplit(_CLAIM_SOURCE_END, 1)[0]
        try:
            located = json.loads(answer or "{}")
        except ValueError:
            continue
        if not isinstance(located, dict):
            continue
        actor = str(located.get("actor") or "").strip().strip(",;:")
        text = compose(actor, str(located.get("predicate") or ""), source)
        if text and text not in seen:
            seen.add(text)
            out.append((source, at.date(), text, actor))
    return out


def distinct_claims(claims: list[Claim]) -> list[Claim]:
    """CLAIMS without those that restate an earlier one (restates: their
    texts' cosine, and whether they share an actor), in order, so the first
    (best-sourced) wording of each fact is the one kept.

    Like on_topic, a failure keeps every claim: this runs inside the hourly
    refresh, and it only ever removes."""
    if len(claims) < 2:
        return claims
    try:
        vectors = vector_store.encode_normalized(vector_store.get_similarity_model(), [c.text for c in claims])
        calibration = restatement_calibration()
        float(calibration["threshold"])
    except Exception:
        logger.exception("Claim restatement check failed — keeping every claim")
        return claims
    kept: list[int] = []
    for i in range(len(claims)):
        if not any(restates(float(vectors[i] @ vectors[j]), claims[i].actor, claims[j].actor, calibration)
                   for j in kept):
            kept.append(i)
    # The gate's pass, for the drift check: a claim dropped as a restatement.
    calibration_drift.observe("restatement", [float(i not in kept) for i in range(len(claims))], 0.5)
    if len(kept) != len(claims):
        logger.info("Dropped %d claim(s) restating an earlier one", len(claims) - len(kept))
    return [claims[i] for i in kept]


def build_facts(claims: list[Claim], limit: int = MAX_FACTS) -> tuple[list[str], list[str], list[str]]:
    """(facts, aligned source names, aligned article URLs): each line on
    the issue page names and links the article it was quoted from.

    Two aligned lists rather than a list of objects so `facts` keeps its
    `list[str]` shape — `previous_facts`, `bsky_posted_facts`, `newFacts`
    and `_issue_signature` all compare fact strings and keep working
    untouched.
    """
    chosen = claims[:limit]
    return [c.text for c in chosen], [c.source_name for c in chosen], [c.source_url for c in chosen]


def build_lede(claims: list[Claim]) -> str:
    """The issue summary: the single best-supported claim, verbatim.

    Deliberately not a synthesis. A synthesis is where the connecting
    language lives, and connecting language is where role reversal and
    editorialising live with it — the summary is exactly the field that
    published "E. Jean Carroll was found guilty" and "Iran War Ends
    Quickly to Lower Prices".
    """
    return claims[0].text if claims else ""


def build_story(claims: list[Claim], shown: list[str]) -> str | None:
    """The full story: every verified claim, grouped under the outlet
    that made it, or None when it would only repeat what the issue page
    already shows (`shown`: its summary and facts).

    No sentence here is written. Each is a verbatim span `compose`
    verified, and the only structure added is the outlet heading
    ("## NPR"), which the issue page renders as a subheading. It replaced
    a model writing 800 characters of prose from the same facts, which
    published a relationship the sources never stated (issue 748), gave
    a House member the wrong office (750) and filled a third with
    nothing (751) — each past the shared grounding checks.
    """
    if not any(c.text not in shown for c in claims):
        return None
    by_outlet: dict[str, list[str]] = {}
    for claim in claims:
        by_outlet.setdefault(claim.source_name or "Other reporting", []).append(claim.text)
    return "\n\n".join(
        f"## {outlet}\n\n{' '.join(texts)}" for outlet, texts in by_outlet.items()
    )
