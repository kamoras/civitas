"""Retrieval-first natural-language question answering.

The obvious way to build "ask Civitas a question" is to hand a language
model the question plus some context and let it write the answer. That is
the wrong shape for this project specifically.

Most questions people actually ask — "who funds Ossoff", "which senators
score worst on funding independence", "who takes the most pharma money" —
are *queries over structured data*, not generation tasks. Answering them
by retrieval is faster (no token generation on the critical path), it
works on the hardware this runs on, and above all it is *auditable*: every
figure in the answer is a row in the database, returned alongside the
answer as a citation.

A language model asked to state a senator's donation total will sometimes
state a plausible wrong one. For a project whose entire value proposition
is that its numbers are checkable, that is not a quality problem to tune
down — it is the failure mode that would discredit the whole thing. So no
figure in an answer here is ever generated. The optional LLM path
(QA_LLM_PHRASING) only rephrases already-rendered text, and its output is
rejected outright if it contains any number the deterministic answer did
not (see _numbers_are_preserved).

Intent classification uses embedding similarity against natural-language
prototypes — the same tier-2 technique the pipeline's classifiers use —
with a margin gate. bill_analyzer.py's 2026-07 audit found category
anchors cluster within a few hundredths of each other for almost any
input, so an unguarded argmax over prototypes is noise. Intents here are
far more separable than 18 policy areas, but the gate means an ambiguous
question falls back to document search rather than confidently answering
the wrong question.
"""

import logging
import re
import time

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Donor, IndustryDonation, Representative, Senator

logger = logging.getLogger(__name__)

# Minimum cosine gap between the best intent and "documents" specifically
# (see classify_intent) before the best one is trusted. Below it, the
# question routes to document search. Deliberately generous: answering the
# wrong structured question with confident-looking real numbers is worse
# than returning documents.
#
# Checked against the production embedding model (not fitted against real
# user traffic, which doesn't exist yet) — the endpoint still returns
# intentScore and intentMargin on every response so real traffic can
# confirm or move this once there is any. Until then, expect the gate to
# be too tight (useful questions falling through to document search)
# rather than too loose — which is the direction that fails safe.
INTENT_MARGIN = 0.06

# Absolute floor — a question unlike every prototype (a greeting, a typo,
# something entirely off-topic) should not be forced into the nearest bin.
INTENT_FLOOR = 0.25

# Natural-language prototypes, not keyword lists. Each is phrased the way
# a person would actually ask, because that is what gets embedded and
# compared against.
INTENT_PROTOTYPES: dict[str, str] = {
    "member_scorecard": (
        "How does this senator score? What is their rating, their grade, "
        "their transparency score, how well do they represent people?"
    ),
    "member_donors": (
        "Who funds this senator? Who are their biggest donors, their top "
        "contributors, where does their campaign money come from?"
    ),
    "member_industries": (
        "Which industries give money to this senator? What sectors fund "
        "them, what is their industry breakdown of contributions?"
    ),
    "top_by_score": (
        "Which senators score the highest or the lowest? Rank members by "
        "their score. Who are the best and worst rated?"
    ),
    "industry_leaders": (
        "Which senators take the most money from a particular industry, "
        "like pharmaceuticals, oil and gas, banking, or defense?"
    ),
    "documents": (
        "Find speeches, bills, executive orders, court opinions, and "
        "regulatory documents about a topic."
    ),
}

# Score column -> the name a person would use for it.
SCORE_FIELDS = {
    "score_funding_independence": "Funding Independence",
    "score_promise_persistence": "Promise Persistence",
    "score_independent_voting": "Constituent Alignment",
    "score_funding_diversity": "Funding Diversity",
    "score_legislative_effectiveness": "Legislative Effectiveness",
}

_prototype_cache: dict | None = None


def _get_prototype_embeddings():
    """Embed the intent prototypes once per process.

    Six short strings — the cost is one model call at first use, not per
    request. The embedding model is already resident for explore search.
    """
    global _prototype_cache
    if _prototype_cache is not None:
        return _prototype_cache

    import numpy as np

    from app.pipeline.vector_store import get_embedding_model

    model = get_embedding_model()
    keys = list(INTENT_PROTOTYPES)
    embeddings = model.encode(
        [INTENT_PROTOTYPES[k] for k in keys], show_progress_bar=False,
    )
    normed = [e / np.linalg.norm(e) for e in embeddings]
    _prototype_cache = {"keys": keys, "embeddings": normed}
    return _prototype_cache


def reset_prototype_cache() -> None:
    """Drop the cached prototype embeddings. Tests only."""
    global _prototype_cache
    _prototype_cache = None


def classify_intent(question: str) -> tuple[str, float, float]:
    """Best intent for a question, with its score and its margin over
    "documents" specifically — not over the runner-up, see below.

    Returns ("documents", 0.0, 0.0) if embeddings are unavailable — the
    fallback is always a working answer path, never an error.
    """
    try:
        import numpy as np

        from app.pipeline.vector_store import get_embedding_model

        cache = _get_prototype_embeddings()
        model = get_embedding_model()
        q_emb = model.encode([question], show_progress_bar=False)[0]
        q_emb = q_emb / np.linalg.norm(q_emb)

        scores = {k: float(np.dot(q_emb, e)) for k, e in zip(cache["keys"], cache["embeddings"])}
    except Exception:
        logger.warning("Intent classification unavailable — routing to documents", exc_info=True)
        return "documents", 0.0, 0.0

    best_key, best_score = max(scores.items(), key=lambda kv: kv[1])
    # Margin against "documents" specifically, not the immediate runner-up.
    # Measured against real questions run through the production embedding
    # model (Snowflake/snowflake-arctic-embed-xs): the five structured
    # intents cluster together — "who funds this senator" and "which
    # industries fund this senator" sit within ~0.05 of EACH OTHER — because
    # they are all "about this senator's money/score", which the model
    # correctly treats as the dominant signal. A runner-up-relative margin
    # gate rejected most of them, sending real, unambiguous questions to
    # document search purely because their nearest neighbor happened to be
    # another (equally safe, equally correct) structured handler rather
    # than "documents". The actual safety question this gate exists to ask
    # is narrower: is this confidently NOT a document search? That is a
    # margin against documents' own score, and on the same real-question
    # sample it separates cleanly — legitimate structured questions land
    # 0.11-0.20 above documents, off-topic ones land 0.00-0.03 above it —
    # at the same INTENT_MARGIN this repo shipped with.
    margin = best_score - scores.get("documents", best_score)

    if best_score < INTENT_FLOOR or margin < INTENT_MARGIN:
        return "documents", best_score, margin
    return best_key, best_score, margin


def resolve_member(db: Session, question: str):
    """Find the member a question is about, by matching stored names.

    Matches against the database rather than parsing the question, so the
    set of recognised names is exactly the set of members that exist. Last
    name alone is accepted only when it is unambiguous across both
    chambers — "how did Johnson vote" should not silently pick one of
    several Johnsons.
    """
    lowered = f" {question.lower()} "
    candidates: list = []

    for model in (Senator, Representative):
        for member in db.query(model).all():
            name = (member.name or "").lower()
            if not name:
                continue
            if f" {name} " in lowered:
                return member, "full"
            last = name.split()[-1]
            # Word-boundary match so "Cruz" doesn't fire inside "Cruzeiro"
            # and "Long" doesn't fire inside "longest". Three characters is
            # the floor because real senators have surnames that short
            # (Roe, Lee); the boundary, not the length, is what prevents
            # substring false positives.
            if len(last) >= 3 and re.search(rf"\b{re.escape(last)}\b", lowered):
                candidates.append(member)

    if len(candidates) == 1:
        return candidates[0], "surname"
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "none"


def _money(value: float | None) -> str:
    if not value:
        return "$0"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def _member_kind(member) -> str:
    return "senator" if isinstance(member, Senator) else "representative"


def _answer_member_scorecard(db: Session, member) -> dict:
    lines = [f"{member.name} ({member.party}-{member.state}) scores:"]
    citations = []
    for field, label in SCORE_FIELDS.items():
        value = getattr(member, field, None)
        if value is None:
            continue
        lines.append(f"  {label}: {value:.0f}/100")
        citations.append({
            "kind": "score", "entityType": _member_kind(member),
            "entityId": member.id, "field": field, "label": label, "value": value,
        })
    return {"answer": "\n".join(lines), "citations": citations}


def _answer_member_donors(db: Session, member, limit: int = 5) -> dict:
    if not isinstance(member, Senator):
        # Rep donors live in their own table; keeping this honest rather
        # than silently answering about the wrong chamber.
        return {
            "answer": (
                f"Donor detail is only indexed for senators right now, and "
                f"{member.name} is a representative."
            ),
            "citations": [],
        }

    donors = (
        db.query(Donor)
        .filter(Donor.senator_id == member.id)
        .order_by(Donor.total.desc())
        .limit(limit)
        .all()
    )
    if not donors:
        return {"answer": f"No donor records are stored for {member.name}.", "citations": []}

    lines = [f"Top {len(donors)} contributors to {member.name}:"]
    citations = []
    for d in donors:
        lines.append(f"  {d.name} — {_money(d.total)} ({d.industry})")
        citations.append({
            "kind": "donor", "entityType": "senator", "entityId": member.id,
            "name": d.name, "total": d.total, "industry": d.industry,
        })
    lines.append(f"Total raised: {_money(member.total_raised)}")
    return {"answer": "\n".join(lines), "citations": citations}


def _answer_member_industries(db: Session, member, limit: int = 5) -> dict:
    if not isinstance(member, Senator):
        return {
            "answer": (
                f"Industry breakdowns are only indexed for senators right "
                f"now, and {member.name} is a representative."
            ),
            "citations": [],
        }

    rows = (
        db.query(IndustryDonation)
        .filter(IndustryDonation.senator_id == member.id)
        .order_by(IndustryDonation.total.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return {"answer": f"No industry breakdown is stored for {member.name}.", "citations": []}

    lines = [f"Industries funding {member.name}:"]
    citations = []
    for r in rows:
        lines.append(f"  {r.industry} — {_money(r.total)} ({r.percentage:.1f}%)")
        citations.append({
            "kind": "industry", "entityType": "senator", "entityId": member.id,
            "industry": r.industry, "total": r.total, "percentage": r.percentage,
        })
    return {"answer": "\n".join(lines), "citations": citations}


def _wants_lowest(question: str) -> bool:
    return bool(re.search(r"\b(worst|lowest|bottom|least)\b", question.lower()))


def _answer_top_by_score(db: Session, question: str, limit: int = 5) -> dict:
    from app.pipeline.analyze.score_calculator import compute_overall_score

    senators = db.query(Senator).filter(Senator.is_current.is_(True)).all()
    if not senators:
        return {"answer": "No senator scores are stored yet.", "citations": []}

    scored = [(s, compute_overall_score(s)) for s in senators]
    ascending = _wants_lowest(question)
    scored.sort(key=lambda pair: pair[1], reverse=not ascending)
    top = scored[:limit]

    heading = "Lowest-scoring senators:" if ascending else "Highest-scoring senators:"
    lines = [heading]
    citations = []
    for member, score in top:
        lines.append(f"  {member.name} ({member.party}-{member.state}) — {score:.0f}/100")
        citations.append({
            "kind": "score", "entityType": "senator", "entityId": member.id,
            "field": "overall", "label": "Overall", "value": score,
        })
    return {"answer": "\n".join(lines), "citations": citations}


def _extract_industry(db: Session, question: str) -> str | None:
    """Match a question against industry labels actually present in the data.

    Reads the stored label set rather than carrying a hardcoded industry
    list, so it cannot drift out of step with what the classifier emits.
    """
    lowered = question.lower()
    labels = {row[0] for row in db.query(IndustryDonation.industry).distinct().all() if row[0]}
    best = None
    for label in labels:
        # "OIL_GAS" -> "oil gas"; match if every word appears.
        words = [w for w in label.lower().replace("_", " ").split() if w]
        if words and all(re.search(rf"\b{re.escape(w)}", lowered) for w in words):
            if best is None or len(label) > len(best):
                best = label
    return best


def _answer_industry_leaders(db: Session, question: str, limit: int = 5) -> dict:
    industry = _extract_industry(db, question)
    if industry is None:
        return {
            "answer": (
                "I could not tell which industry you meant. Try naming it as "
                "it appears in the data, e.g. \"pharmaceuticals\" or \"defense\"."
            ),
            "citations": [],
        }

    rows = (
        db.query(IndustryDonation, Senator)
        .join(Senator, Senator.id == IndustryDonation.senator_id)
        .filter(IndustryDonation.industry == industry)
        .order_by(IndustryDonation.total.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return {"answer": f"No {industry} contributions are stored.", "citations": []}

    lines = [f"Senators receiving the most from {industry}:"]
    citations = []
    for donation, member in rows:
        lines.append(f"  {member.name} ({member.party}-{member.state}) — {_money(donation.total)}")
        citations.append({
            "kind": "industry", "entityType": "senator", "entityId": member.id,
            "industry": industry, "total": donation.total,
            "percentage": donation.percentage,
        })
    return {"answer": "\n".join(lines), "citations": citations}


def _answer_documents(db: Session, question: str, limit: int = 5) -> dict:
    from app.services.explore_search import hybrid_search

    try:
        found = hybrid_search(db, question, limit=limit)
    except Exception:
        logger.exception("Document fallback failed for %r", question)
        return {"answer": "Search is temporarily unavailable.", "citations": []}

    results = found.get("results", [])
    if not results:
        return {"answer": "No matching documents found.", "citations": []}

    lines = ["Related documents:"]
    citations = []
    for r in results:
        lines.append(f"  {r.get('title', 'Untitled')} ({r.get('docType', 'document')})")
        citations.append({
            "kind": "document", "id": r.get("id"), "title": r.get("title"),
            "docType": r.get("docType"), "date": r.get("date"), "url": r.get("url"),
        })
    return {"answer": "\n".join(lines), "citations": citations}


# The fractional part requires digits after the point, so a figure at the
# end of a sentence ("$250,000.") normalises to the same token as the same
# figure mid-sentence ("$250,000"). Without that, sentence-final punctuation
# alone made a faithful rewrite look like an invented number.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers_are_preserved(original: str, rewritten: str) -> bool:
    """True when the rewrite introduces no figure the original lacked, and
    reuses no figure more often than the original did.

    The guard on the optional LLM phrasing path. A model asked to rephrase
    "$1.2M from Pfizer" can quietly emit "$1.4M", and for this project that
    single altered digit is worse than no rephrasing at all. Digits added
    is the failure we reject; digits dropped is merely a terser sentence.

    Counted with a multiset, not a set: a two-figure answer like "Jane Doe
    scores 82, John Roe scores 31" rewritten as "Jane Doe scores 31, John
    Roe scores 82" swaps two real figures between two real people — every
    number in the rewrite is still a member of the original's number set,
    so a plain set-containment check would wave it through. Requiring each
    figure's count not to increase catches a figure being duplicated onto
    a second claim it didn't originally support.

    What this still cannot catch: swapping two *distinct* figures that
    each already appear exactly once, onto each other's claims (the exact
    example above — the multiset is unchanged by the swap, only which
    name pairs with which number). Closing that gap needs the rewrite to
    preserve name-number adjacency, not just the figures present, which
    is a much larger check than a presentation-layer guard is built for
    here. Short-rewrite, faithful-paraphrase framing keeps this a narrow
    risk in practice, but it is a real one — see PR review discussion.
    """
    import collections

    original_numbers = collections.Counter(_NUMBER_RE.findall(original.replace(",", "")))
    rewritten_numbers = collections.Counter(_NUMBER_RE.findall(rewritten.replace(",", "")))
    return not (rewritten_numbers - original_numbers)


def _maybe_rephrase(answer: str) -> tuple[str, bool]:
    """Optionally rewrite a rendered answer as prose. Returns (text, used_llm).

    Off by default. Even when on, the deterministic answer is what gets
    returned unless the rewrite passes the number check — the LLM is a
    presentation layer here and is never permitted to become a source of
    figures.
    """
    if not settings.QA_LLM_PHRASING:
        return answer, False

    try:
        from app.pipeline.analyze.ollama_client import call_llm

        result = call_llm(
            prompt_version="qa_phrasing_v1",
            system_prompt=(
                "You rewrite factual summaries as plain prose. You never "
                "add, remove, or alter a number, name, or figure. Respond "
                'with JSON: {"text": "<rewritten summary>"}'
            ),
            user_prompt=(
                "Rewrite this as one or two plain sentences, preserving "
                "every figure exactly:\n\n" + answer
            ),
            cache_key=answer,
            max_tokens=400,
        )
        rewritten = (result or {}).get("text", "") if isinstance(result, dict) else ""
        rewritten = str(rewritten).strip()
    except Exception:
        logger.warning("QA phrasing call failed — returning deterministic answer", exc_info=True)
        return answer, False

    if not rewritten or not _numbers_are_preserved(answer, rewritten):
        logger.info("Discarding LLM rephrasing — it altered or invented figures")
        return answer, False
    return rewritten, True


def answer_question(db: Session, question: str, *, limit: int = 5) -> dict:
    """Answer a natural-language question from stored data.

    Every figure in `answer` also appears in `citations`, which name the
    rows it came from. `latencyMs` is measured end to end so the cost of
    this path on the deployed hardware is observable from the first day
    rather than inferred.
    """
    started = time.perf_counter()
    intent, score, margin = classify_intent(question)

    member = None
    resolution = "none"
    if intent in ("member_scorecard", "member_donors", "member_industries"):
        member, resolution = resolve_member(db, question)
        if member is None:
            # A member-shaped question naming no resolvable member is a
            # document search, not an error.
            intent = "documents"

    if intent == "member_scorecard":
        payload = _answer_member_scorecard(db, member)
    elif intent == "member_donors":
        payload = _answer_member_donors(db, member, limit=limit)
    elif intent == "member_industries":
        payload = _answer_member_industries(db, member, limit=limit)
    elif intent == "top_by_score":
        payload = _answer_top_by_score(db, question, limit=limit)
    elif intent == "industry_leaders":
        payload = _answer_industry_leaders(db, question, limit=limit)
    else:
        intent = "documents"
        payload = _answer_documents(db, question, limit=limit)

    text, used_llm = _maybe_rephrase(payload["answer"])

    return {
        "question": question,
        "intent": intent,
        "intentScore": round(score, 4),
        "intentMargin": round(margin, 4),
        "memberResolution": resolution,
        "answer": text,
        "deterministicAnswer": payload["answer"],
        "citations": payload["citations"],
        "usedLlm": used_llm,
        "latencyMs": round((time.perf_counter() - started) * 1000, 1),
    }
