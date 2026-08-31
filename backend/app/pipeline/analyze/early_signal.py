"""Early-signal reporting: draft a deliberately hedged, primary-source-only
ActionIssue from a Senate roll-call vote before conventional news covers it.

Phase 1 scope, deliberately narrow (see the approved plan): Senate final-
passage votes only, one source type. A completed roll-call tally is a
certified fact, not an interpretation — the one candidate among the sources
researched where "we could be wrong about what happened" risk is close to
zero. The remaining judgment call, "is this worth a story," is validated
after the fact by real press coverage (see action_center.py's promotion/
expiry wiring), not asserted here from an untested heuristic.

Nothing in this module posts anything publicly. It only ever creates an
ActionIssue with status=DEVELOPING; action_center.py is responsible for
excluding those from Bluesky posting until promoted.
"""

import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.models import ActionIssue, ActionIssueStatus
from app.pipeline.analyze import action_metrics
from app.pipeline.analyze.bill_analyzer import classify_policy_area, recent_roll_call_key
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
    validate_facts,
)
from app.pipeline.analyze.ollama_client import call_llm, extract_json
from app.pipeline.fetch.congress import fetch_recent_roll_calls
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

EARLY_SIGNAL_PROMPT_VERSION = "early-signal-v1"

# Deliberately conservative and NOT calibrated from data — there is no
# history yet. action_metrics logs early_signal_confirmed/_expired so a
# real window can replace this once enough runs have resolved one way or
# the other (same measure-before-enforcing discipline as every other
# threshold in this pipeline).
CONFIRMATION_WINDOW_HOURS = 48

# Cache TTL for the near-real-time roll-call poll — see fetch_recent_roll_
# calls'/fetch_roll_call_vote's docstrings. Short enough that a vote is
# noticed within the hour it happens, not up to the 72h pipeline default.
_ROLL_CALL_POLL_MAX_AGE_HOURS = 1

# Only the two most recent votes per session — this poll runs hourly, so
# anything further back would already have been seen (or gated out) on a
# prior run. Kept small to bound the LLM/grounding cost of a stage that
# runs every hour regardless of whether Congress is in session.
_ROLL_CALL_POLL_COUNT_PER_SESSION = 2

# Senate.gov's own vocabulary for a final-passage vote (lowercased
# substring match against `question`/`voteTitle`). Deliberately narrow for
# phase 1 — nominations, cloture, and amendment votes are excluded even
# though some are newsworthy, so the initial gate stays conservative;
# widen once real confirm/expire outcomes justify it.
_FINAL_PASSAGE_MARKERS = ("on passage of the bill", "on the joint resolution")


def _senate_vote_url(congress: int, session: int, roll_number: int) -> str:
    padded = str(roll_number).zfill(5)
    return (
        f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
        f"vote{congress}{session}/vote_{congress}_{session}_{padded}.xml"
    )


def _vote_margin_ratio(vote: dict) -> float:
    casts = [m.get("voteCast", "") for m in vote.get("members", [])]
    yeas = sum(1 for c in casts if c == "Yea")
    nays = sum(1 for c in casts if c == "Nay")
    if yeas + nays == 0:
        return 0.0
    return abs(yeas - nays) / (yeas + nays)


def _is_final_passage(vote: dict) -> bool:
    text = f"{vote.get('question', '')} {vote.get('voteTitle', '')}".lower()
    return any(marker in text for marker in _FINAL_PASSAGE_MARKERS)


def _vote_source_text(vote: dict) -> str:
    """The ground-truth text a hedged draft's grounding check runs against
    — everything the vote record itself states, nothing more."""
    casts = [m.get("voteCast", "") for m in vote.get("members", [])]
    yeas = sum(1 for c in casts if c == "Yea")
    nays = sum(1 for c in casts if c == "Nay")
    not_voting = sum(1 for c in casts if c not in ("Yea", "Nay"))
    return (
        f"Roll call vote {vote.get('rollNumber')}, {vote.get('congress')}th "
        f"Congress, session {vote.get('session')}, dated {vote.get('voteDate')}. "
        f"Question: {vote.get('question', '')}. "
        f"Document: {vote.get('documentTitle', '')}. "
        f"Result: {yeas} Yea, {nays} Nay, {not_voting} Not Voting."
    )


_EARLY_SIGNAL_SYSTEM_PROMPT = """\
You are a nonpartisan civic information analyst. You are drafting a \
PROVISIONAL report about a Senate floor vote that just occurred, based \
ONLY on the official vote record below — no news coverage of this vote \
exists yet. Report only what the vote record states: the matter voted \
on, the outcome, and the tally. Do not speculate about what happens \
next, why senators voted as they did, or how this will be covered. \
State plainly that broader press coverage has not yet appeared. Never \
advocate for or against any policy, and never state or imply that the \
outcome was warranted, justified, or expected."""

_EARLY_SIGNAL_PROMPT_TEMPLATE = """\
A Senate roll-call vote just occurred. Below is the official vote record. \
Produce a JSON object with these fields:

- "title": A concise, neutral headline for this vote (max 15 words), \
naming the actual matter voted on and its outcome.
- "summary": 2-3 factual sentences describing what was voted on and the \
result (the tally, e.g. 60-40), stated directly from the vote record \
below. Include one sentence noting this is based on the official record \
and that broader news coverage has not yet appeared.
- "facts": An array of 2-4 factual bullet points — the vote tally, the \
matter's official title, the date, and the chamber. Every fact must be \
directly stated in the vote record below — never infer intent or \
predict what happens next.

Vote record:
{vote_text}

Respond with ONLY the JSON object, no other text.
"""


def _draft_developing_issue(vote: dict, db: Session) -> tuple[str, str, list[str]] | None:
    """Generate a hedged, grounded (title, summary, facts) from a vote
    record, or None if two attempts both fail grounding.

    Same two-attempt retry shape as _retry_until_grounded, but this
    domain's correction prompt and validators are its own — the vote
    record is the only source, so `_fix_impossible_senate_vote_counts`/
    `_validate_politician_roles` (news-cluster-specific) don't apply.
    """
    source_text = _vote_source_text(vote)
    user_prompt = _EARLY_SIGNAL_PROMPT_TEMPLATE.format(vote_text=source_text)

    for attempt in range(1, 3):
        prompt = user_prompt
        if attempt > 1:
            prompt += (
                "\n\nYour previous response was rejected. Use ONLY the "
                "vote record above: do not state any number not in it, do "
                "not predict what happens next, do not evaluate whether "
                "the outcome was warranted, and do not omit the note that "
                "broader coverage has not yet appeared."
            )
        result = call_llm(
            prompt_version=EARLY_SIGNAL_PROMPT_VERSION,
            system_prompt=_EARLY_SIGNAL_SYSTEM_PROMPT,
            user_prompt=prompt,
            cache_key=None,
            db_session=db,
            max_tokens=512,
            num_ctx=2048,
        )
        if isinstance(result, str):
            result = extract_json(result)
        if not isinstance(result, dict):
            continue

        title = (result.get("title") or "").strip()
        summary = (result.get("summary") or "").strip()
        facts = validate_facts(result.get("facts", []), source_text=source_text)
        combined = f"{title} {summary} " + " ".join(facts)

        reasons = (
            grounding_violations(combined, source_text)
            + hedge_and_editorializing_violations(combined, allow_hedging=True)
        )
        if title and summary and not reasons:
            return title, summary, facts
        logger.warning(
            "Early-signal draft failed grounding (attempt %d): %s",
            attempt, "; ".join(reasons) or "empty title/summary",
        )

    return None


def _fetch_recent_votes(db: Session) -> list[dict]:
    """Fetch the latest Senate roll calls across both sessions of the
    current Congress, deduped by identity — same pattern senate_pipeline
    already uses for its own multi-session recent-vote sweep."""
    async def _fetch() -> list[dict]:
        async with make_async_client() as client:
            votes: list[dict] = []
            for session_num in (1, 2):
                session_votes = await fetch_recent_roll_calls(
                    client, db,
                    congress=settings.CURRENT_CONGRESS,
                    session_number=session_num,
                    count=_ROLL_CALL_POLL_COUNT_PER_SESSION,
                    max_age_hours=_ROLL_CALL_POLL_MAX_AGE_HOURS,
                )
                votes.extend(session_votes)
            return votes

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()


def check_roll_call_signals(db: Session) -> int:
    """Poll recent Senate roll calls, gate for notability, draft and store
    a DEVELOPING ActionIssue for any genuinely new, non-procedural,
    final-passage vote. Returns the number of new rows created.

    Called from action_center._run_refresh, before the news-fetch stage,
    on the existing hourly cadence — a roll call only changes when
    Congress votes, so no separate scheduled job is needed.
    """
    created = 0
    seen_keys: set[str] = set()

    for vote in _fetch_recent_votes(db):
        key = recent_roll_call_key(vote)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        action_metrics.increment("early_signal_votes_seen")
        margin = _vote_margin_ratio(vote)
        action_metrics.increment_bucket("early_signal_vote_margin", margin)

        doc_title = vote.get("documentTitle") or vote.get("voteTitle") or ""
        area, _ = classify_policy_area(doc_title)
        if area == "PROCEDURAL":
            action_metrics.increment("early_signal_gate_procedural")
            continue

        if not _is_final_passage(vote):
            action_metrics.increment("early_signal_gate_not_final_passage")
            continue

        action_metrics.increment("early_signal_gate_candidate")

        vote_url = _senate_vote_url(
            vote.get("congress"), vote.get("session"), vote.get("rollNumber"),
        )
        already_exists = (
            db.query(ActionIssue)
            .filter(ActionIssue.primary_source_url == vote_url)
            .first()
        )
        if already_exists:
            continue

        drafted = _draft_developing_issue(vote, db)
        if drafted is None:
            action_metrics.increment("early_signal_gate_grounding_failed")
            continue
        title, summary, facts = drafted

        row = ActionIssue(
            date=vote.get("voteDate") or utcnow().strftime("%Y-%m-%d"),
            rank=999,  # placeholder — renumbered alongside every other row each run
            title=title[:500],
            summary=summary,
            facts=json.dumps(facts),
            source_urls=json.dumps([vote_url]),
            source_names=json.dumps(["Senate.gov roll call record"]),
            is_current=True,
            status=ActionIssueStatus.DEVELOPING,
            source_type="roll_call_vote",
            primary_source_url=vote_url,
            confirmation_deadline=utcnow() + timedelta(hours=CONFIRMATION_WINDOW_HOURS),
            primary_article_date=vote.get("voteDate"),
        )
        db.add(row)
        created += 1
        action_metrics.increment("early_signal_created")
        logger.info("Created developing issue from roll call %s: '%s'", key, title[:60])

    if created:
        db.flush()
    return created


def expire_stale_developing_issues(db: Session, now) -> int:
    """Retire any DEVELOPING issue past its confirmation_deadline — never
    deletes, same "flip a boolean, render the true state" mechanic as
    _retire_untouched_issues and BallotMeasure.status. A row promoted this
    run is no longer status=DEVELOPING by the time this runs, so no
    matched-ids bookkeeping is needed here (unlike _retire_untouched_
    issues, which retires by absence from a fresh cluster pass).
    """
    stale = (
        db.query(ActionIssue)
        .filter(
            ActionIssue.status == ActionIssueStatus.DEVELOPING,
            ActionIssue.is_current == True,  # noqa: E712
            ActionIssue.confirmation_deadline.isnot(None),
            ActionIssue.confirmation_deadline < now,
        )
        .all()
    )
    for row in stale:
        row.is_current = False
        action_metrics.increment("early_signal_expired")
        action_metrics.increment(f"early_signal_expired_{row.source_type or 'unknown'}")
        logger.info("Expired unconfirmed developing issue %d: '%s'", row.id, row.title[:60])
    return len(stale)
