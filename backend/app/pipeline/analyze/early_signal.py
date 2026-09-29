"""Early-signal reporting: draft a deliberately hedged, primary-source-only
ActionIssue from a Senate or House roll-call vote before conventional news
covers it.

Phase 1 scope, deliberately narrow (see the approved plan): final-passage
votes only, one source type per chamber. A completed roll-call tally is a
certified fact, not an interpretation — the one candidate among the sources
researched where "we could be wrong about what happened" risk is close to
zero. The remaining judgment call, "is this worth a story," is validated
after the fact by real press coverage (see action_center.py's promotion/
expiry wiring), not asserted here from an untested heuristic.

Nothing in this module posts anything publicly. It only ever creates an
ActionIssue with status=DEVELOPING; action_center.py is responsible for
excluding those from Bluesky posting until promoted.

A draft is filled from its record (the roll call, or the Federal Register
entry) by a fixed template, not written by the model: the model's drafts characterized the record ("a
narrow 77-22", 2026-09-28) in ways no check lists in advance. A vote on a
bill a current issue already covers is not drafted, and a draft whose bill
news coverage has since reached as a separate issue is retired
(retire_covered_developing_issues): the reporting is the fuller story.
"""

import asyncio
import json
import logging
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.models import ActionIssue, ActionIssueStatus, RepSponsoredBill, SponsoredBill
from app.pipeline.analyze import action_metrics
from app.pipeline.analyze.bill_analyzer import classify_policy_area, recent_roll_call_key
from app.pipeline.fetch.congress import fetch_recent_house_roll_calls, fetch_recent_roll_calls
from app.pipeline.fetch.daily_digest import first_bill_id
from app.pipeline.fetch.federal_register import fetch_recent_significant_rules
from app.pipeline.fetch.floor_logs import bill_id_from_number
from app.pipeline.transform.normalize_votes import vote_date_iso
from app.services.bill_service import names_phrase, short_title
from app.services.congress_service import bill_label
from app.time_utils import utcnow

logger = logging.getLogger(__name__)


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
# prior run. Kept small to bound the requests of a stage that runs every
# hour regardless of whether Congress is in session.
_ROLL_CALL_POLL_COUNT_PER_SESSION = 2

# Same reasoning as _ROLL_CALL_POLL_MAX_AGE_HOURS, for the Federal Register
# significant-rule poll.
_RULE_POLL_MAX_AGE_HOURS = 1

# Senate.gov's own vocabulary for a final-passage vote (lowercased
# substring match against `question`/`voteTitle`). Deliberately narrow for
# phase 1 — nominations, cloture, and amendment votes are excluded even
# though some are newsworthy, so the initial gate stays conservative;
# widen once real confirm/expire outcomes justify it.
_FINAL_PASSAGE_MARKERS = ("on passage of the bill", "on the joint resolution")

# clerk.house.gov's equivalent vocabulary. "On Motion to Suspend the Rules
# and Pass" is a genuine final-passage mechanism (confirmed live against
# real vote XML), not a procedural motion, so it's included alongside the
# House's own "On Passage" phrasing.
_HOUSE_FINAL_PASSAGE_MARKERS = ("on passage", "suspend the rules and pass")


def _chamber_labels(vote: dict) -> tuple[str, str]:
    """(display chamber name, plural noun for its members) for the draft's
    text — the vote dict only ever tags House votes
    with chamber="House" (see parse_house_vote_xml), so absence means
    Senate."""
    if vote.get("chamber") == "House":
        return "House of Representatives", "representatives"
    return "Senate", "senators"


def _senate_vote_url(congress: int, session: int, roll_number: int) -> str:
    padded = str(roll_number).zfill(5)
    return (
        f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
        f"vote{congress}{session}/vote_{congress}_{session}_{padded}.xml"
    )


def _house_vote_url(year: int, roll_number: int) -> str:
    return f"https://clerk.house.gov/evs/{year}/roll{roll_number}.xml"


def _vote_url(vote: dict) -> str:
    if vote.get("chamber") == "House":
        return _house_vote_url(vote.get("year"), vote.get("rollNumber"))
    return _senate_vote_url(vote.get("congress"), vote.get("session"), vote.get("rollNumber"))


def _vote_margin_ratio(vote: dict) -> float:
    casts = [m.get("voteCast", "") for m in vote.get("members", [])]
    yeas = sum(1 for c in casts if c == "Yea")
    nays = sum(1 for c in casts if c == "Nay")
    if yeas + nays == 0:
        return 0.0
    return abs(yeas - nays) / (yeas + nays)


def _is_final_passage(vote: dict) -> bool:
    text = f"{vote.get('question', '')} {vote.get('voteTitle', '')}".lower()
    markers = _HOUSE_FINAL_PASSAGE_MARKERS if vote.get("chamber") == "House" else _FINAL_PASSAGE_MARKERS
    return any(marker in text for marker in markers)


def _tally(vote: dict) -> tuple[int, int, int]:
    casts = [m.get("voteCast", "") for m in vote.get("members", [])]
    yeas = sum(1 for c in casts if c == "Yea")
    nays = sum(1 for c in casts if c == "Nay")
    return yeas, nays, len(casts) - yeas - nays


def _compose_developing_issue(vote: dict) -> tuple[str, str, list[str]]:
    """(title, summary, facts) for a vote, every word either the template's
    or the vote record's own: the measure as the chamber names it, the
    question, the result, the tally and the date."""
    chamber_name, _ = _chamber_labels(vote)
    short = "House" if vote.get("chamber") == "House" else "Senate"
    measure = (vote.get("documentName") or vote.get("voteTitle") or "the measure").strip()
    question = (vote.get("question") or "").strip()
    result = (vote.get("result") or "").strip()
    measure_title = (vote.get("documentTitle") or "").strip()
    yeas, nays, not_voting = _tally(vote)
    day = vote_date_iso(vote.get("voteDate"))

    title = f"{short} vote on {measure}: {result}, {yeas}-{nays}" if result else f"{short} vote on {measure}, {yeas}-{nays}"
    summary = (
        f"The {short} voted {yeas} to {nays}{f' on {day}' if day else ''} "
        f"on the question \"{question}\" for {measure}{f'. Result: {result}' if result else ''}. "
        "This is from the chamber's official roll-call record; news coverage of the vote has not appeared yet."
    )
    facts = [f"Tally: {yeas} yea, {nays} nay, {not_voting} not voting."]
    if measure_title and measure_title != measure:
        facts.append(f"{measure}: {measure_title}")
    facts.append(f"Chamber: {chamber_name}{f', {day}' if day else ''}.")
    return title[:500], summary, facts


def _bill_names(db: Session, bill_id: str) -> list[str]:
    """What an issue can call the bill: its number, as the Record prints
    it and as the site writes it, and its short title (bill_service)."""
    names = {bill_id.lower()}
    if label := bill_label(bill_id):
        names.add(label.lower())
    for model in (SponsoredBill, RepSponsoredBill):
        for (title,) in db.query(model.title).filter(model.bill_id == bill_id, model.congress == settings.CURRENT_CONGRESS):
            if name := short_title(title):
                names.add(name)
    return sorted(names)


def _issue_bill_ids(issue: ActionIssue) -> set[str]:
    try:
        entries = json.loads(issue.related_bill_ids or "[]")
    except (TypeError, ValueError):
        return set()
    return {e["id"].upper() for e in entries if isinstance(e, dict) and e.get("id")}


def _covering_issue(db: Session, bill_id: str, names: list[str], exclude_id: int | None = None) -> ActionIssue | None:
    """A current, reported (not developing) issue about the bill: one that
    records it among its related bills, or names it in its title, summary
    or facts."""
    issues = db.query(ActionIssue).filter(
        ActionIssue.is_current == True,  # noqa: E712
        ActionIssue.status != ActionIssueStatus.DEVELOPING,
    ).all()
    for issue in issues:
        if issue.id == exclude_id:
            continue
        if bill_id in _issue_bill_ids(issue):
            return issue
        text = f"{issue.title} {issue.summary} {issue.facts or ''}".lower()
        if any(names_phrase(text, name) for name in names):
            return issue
    return None


def _fetch_recent_votes(db: Session) -> list[dict]:
    """Fetch the latest Senate and House roll calls, deduped by identity —
    Senate is swept across both sessions of the current Congress (same
    pattern senate_pipeline uses for its own multi-session sweep); House
    roll calls are numbered per calendar year instead, so no session loop
    is needed there."""
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
            house_votes = await fetch_recent_house_roll_calls(
                client, db,
                year=utcnow().year,
                count=_ROLL_CALL_POLL_COUNT_PER_SESSION,
                max_age_hours=_ROLL_CALL_POLL_MAX_AGE_HOURS,
            )
            votes.extend(house_votes)
            return votes

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()


def check_roll_call_signals(db: Session, today: str | None = None) -> int:
    """Poll recent Senate and House roll calls, gate for notability, draft
    and store a DEVELOPING ActionIssue for any genuinely new, non-
    procedural, final-passage vote. Returns the number of new rows created.

    Called from action_center._run_refresh, before the news-fetch stage,
    on the existing hourly cadence — a roll call only changes when
    Congress votes, so no separate scheduled job is needed. `today` is the
    refresh's own date, which every issue it shows carries: the Action
    Center lists the latest date's issues, and a Senate vote's raw date
    ("September 28, 2026,  09:42 PM") sorted after every ISO date and hid
    them all.
    """
    today = today or utcnow().strftime("%Y-%m-%d")
    created = 0
    seen_keys: set[str] = set()

    for vote in _fetch_recent_votes(db):
        chamber = vote.get("chamber") or "Senate"
        # recent_roll_call_key is congress-session-rollNumber only — House
        # and Senate roll numbers are independent per-chamber sequences, so
        # without the chamber prefix a House and Senate vote sharing the
        # same numbers would collide and one would be silently dropped.
        key = f"{chamber}-{recent_roll_call_key(vote)}"
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

        vote_url = _vote_url(vote)
        already_exists = (
            db.query(ActionIssue)
            .filter(ActionIssue.primary_source_url == vote_url)
            .first()
        )
        if already_exists:
            continue

        bill_id = bill_id_from_number(vote.get("documentName"))
        if bill_id and _covering_issue(db, bill_id, _bill_names(db, bill_id)) is not None:
            action_metrics.increment("early_signal_gate_already_covered")
            continue

        title, summary, facts = _compose_developing_issue(vote)

        is_house = chamber == "House"
        row = ActionIssue(
            date=today,
            rank=999,  # placeholder — renumbered alongside every other row each run
            title=title[:500],
            summary=summary,
            facts=json.dumps(facts),
            source_urls=json.dumps([vote_url]),
            source_names=json.dumps(
                ["Clerk of the House roll call record" if is_house else "Senate.gov roll call record"]
            ),
            is_current=True,
            status=ActionIssueStatus.DEVELOPING,
            source_type="house_roll_call_vote" if is_house else "senate_roll_call_vote",
            primary_source_url=vote_url,
            confirmation_deadline=utcnow() + timedelta(hours=CONFIRMATION_WINDOW_HOURS),
            primary_article_date=vote_date_iso(vote.get("voteDate")) or today,
            related_bill_ids=json.dumps([{"name": bill_label(bill_id) or bill_id, "id": bill_id}] if bill_id else []),
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


def retire_covered_developing_issues(db: Session) -> int:
    """Retire a current vote draft whose bill a reported issue now covers.
    News that the matching pass didn't join to the draft (it reads as its
    own story) otherwise leaves two issues about one vote, the draft's the
    thinner. The draft's bill is the one it recorded, or for a draft from
    before it recorded one, the first bill its text names."""
    retired = 0
    drafts = db.query(ActionIssue).filter(
        ActionIssue.status == ActionIssueStatus.DEVELOPING,
        ActionIssue.is_current == True,  # noqa: E712
        ActionIssue.source_type.in_(("senate_roll_call_vote", "house_roll_call_vote")),
    ).all()
    for draft in drafts:
        bill_id = next(iter(_issue_bill_ids(draft)), None) or first_bill_id(f"{draft.title} {draft.summary}")
        if not bill_id:
            continue
        covering = _covering_issue(db, bill_id, _bill_names(db, bill_id), exclude_id=draft.id)
        if covering is None:
            continue
        draft.is_current = False
        retired += 1
        action_metrics.increment("early_signal_retired_covered")
        logger.info("Retired developing issue %d: issue %d covers %s", draft.id, covering.id, bill_id)
    return retired


# A summary quotes the rule's abstract up to this many characters, ending
# at a sentence; the whole abstract is among the facts.
_ABSTRACT_SUMMARY_CHARS = 400


# A period that ends a word like these doesn't end the sentence. The dotted
# forms ("U.S.", "U.S.C.") and initials are caught by shape in _ends_sentence.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "co", "corp", "ltd",
    "no", "nos", "sec", "secs", "pub", "vol", "fig", "dept", "gov", "gen",
})
_SENTENCE_END = re.compile(r"[.?!](?=\s+[\"“(]?[A-Z])")


def _ends_sentence(text: str, at: int) -> bool:
    """Whether the mark at `at` (followed by a capitalised word) ends a
    sentence: not the period of an initial ("John Q. Public"), a dotted
    abbreviation ("the U.S. Fish and Wildlife Service") or a title."""
    if text[at] != ".":
        return True
    word = text[:at].rsplit(" ", 1)[-1].lstrip("(\"“")
    return not ("." in word or len(word) == 1 or word.lower() in _ABBREVIATIONS)


def _first_sentences(text: str, limit: int) -> str:
    """The abstract's opening sentences, whole, within `limit` characters;
    empty when even the first is longer."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    ends = [m.start() for m in _SENTENCE_END.finditer(text, 0, limit) if _ends_sentence(text, m.start())]
    return text[:ends[-1] + 1] if ends else ""


def _compose_developing_rule_issue(rule: dict) -> tuple[str, str, list[str]]:
    """(title, summary, facts) for a significant final rule, every word
    either the template's or the Federal Register record's own: the
    agency, the rule's title, its abstract, the publication date and the
    document number. The model's drafts were dropped with the vote
    drafts', for the same reason (the module docstring)."""
    agencies = ", ".join(rule.get("agencies") or []) or "A federal agency"
    rule_title = " ".join((rule.get("title") or "").split())
    day = rule.get("publicationDate") or ""
    number = rule.get("documentNumber") or ""
    abstract = " ".join((rule.get("abstract") or "").split())
    opening = _first_sentences(abstract, _ABSTRACT_SUMMARY_CHARS)

    title = f"{agencies} final rule: {rule_title}"
    summary = (
        f"{agencies} published the final rule \"{rule_title}\" in the Federal Register"
        f"{f' on {day}' if day else ''}. {opening + ' ' if opening else ''}"
        "This is from the Federal Register; news coverage of the rule has not appeared yet."
    )
    # A record missing its number or date says less, not "document , published ."
    where = ["Federal Register document" + (f" {number}" if number else "")]
    if day:
        where.append(f"published {day}")
    facts = [f"Agency: {agencies}.", ", ".join(where) + "."]
    if abstract:
        facts.append(f"Abstract: {abstract}")
    return title[:500], summary, facts


def _fetch_recent_rules(db: Session) -> list[dict]:
    """Fetch recently published significant Federal Register rules — same
    sync-via-new-event-loop pattern as _fetch_recent_votes."""
    async def _fetch() -> list[dict]:
        async with make_async_client() as client:
            return await fetch_recent_significant_rules(
                client, db, max_age_hours=_RULE_POLL_MAX_AGE_HOURS,
            )

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch())
    finally:
        loop.close()


def check_federal_register_signals(db: Session, today: str | None = None) -> int:
    """Poll recently published significant Federal Register rules, draft
    and store a DEVELOPING ActionIssue for any genuinely new one. Returns
    the number of new rows created.

    Same non-fatal, independent-of-the-news-fetch placement as
    check_roll_call_signals — see action_center._run_refresh. No
    classify_policy_area gate here: unlike a roll call (where the vote
    TYPE can be purely procedural regardless of subject matter),
    "significant under EO 12866" is already OMB's own substantive-impact
    determination — the fetcher's own notability gate, not a raw feed of
    every published document.
    """
    created = 0
    for rule in _fetch_recent_rules(db):
        doc_number = rule.get("documentNumber")
        if not doc_number:
            continue

        action_metrics.increment("early_signal_rules_seen")

        rule_url = rule.get("htmlUrl", "")
        already_exists = (
            db.query(ActionIssue)
            .filter(ActionIssue.primary_source_url == rule_url)
            .first()
        )
        if already_exists:
            continue

        title, summary, facts = _compose_developing_rule_issue(rule)

        row = ActionIssue(
            date=today or utcnow().strftime("%Y-%m-%d"),
            rank=999,  # placeholder — renumbered alongside every other row each run
            title=title[:500],
            summary=summary,
            facts=json.dumps(facts),
            source_urls=json.dumps([rule_url]),
            source_names=json.dumps(["Federal Register"]),
            is_current=True,
            status=ActionIssueStatus.DEVELOPING,
            source_type="federal_register_significant_rule",
            primary_source_url=rule_url,
            confirmation_deadline=utcnow() + timedelta(hours=CONFIRMATION_WINDOW_HOURS),
            primary_article_date=rule.get("publicationDate"),
        )
        db.add(row)
        created += 1
        action_metrics.increment("early_signal_created")
        logger.info("Created developing issue from FR rule %s: '%s'", doc_number, title[:60])

    if created:
        db.flush()
    return created
