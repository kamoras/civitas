"""Posts race-coverage updates to Bluesky (2026-07, midterm-elections feature).

Same shape as bluesky_poster.py's ActionIssue posting: one grounded,
LLM-generated sentence per notable coverage item, verified mechanically
via grounding.py before it ships. Never a bare link — the user's own
requirement is that a post must say something, briefly and accurately,
not just paste a URL.

Eligibility is stricter than the on-site feed (2026-07 review F9): only
items matched on the "full_name" basis (election_coverage.py) can be
posted. The race framing in a post ("the GA Senate race") is a claim; for
a full-name match it is grounded in an FEC roster fact — that named
candidate IS on that race's ballot per FEC filings — which is included in
the grounding source material as an explicit roster-fact line rather than
smuggled in as unexaminable context. Weaker surname+state matches stay
display-only on the site, where the source text is shown verbatim next to
its link and a reader can judge the association themselves; an automated
post asserts the association in Civitas's own voice, which demands the
stronger basis.

Verified directly (not assumed): grounding_violations' electoral-claims
check is already permissive when the SOURCE material itself contains
genuine electoral vocabulary (see tests/test_grounding.py's
test_electoral_framing_grounded_when_source_covers_election) — it only
rejects electoral framing invented from non-electoral source text.

Volume controls (2026-07 review M4 — the per-run cap alone was not
conservative at the 15-minute election-season cadence, 96 runs/day):
  - MAX_POSTS_PER_RUN per pass,
  - MAX_POSTS_PER_DAY across passes (counted from actually-published
    items' bsky_posted flag, not the considered-marker),
  - one post per race per RACE_COOLDOWN_HOURS.
"""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.candidate_dedup import resolve_candidate_id
from app.config import settings
from app.models import Candidate, Race, RaceCoverageItem
from app.pipeline.analyze.bluesky_utils import publish_post, strip_hashtags_and_truncate
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
)
from app.pipeline.analyze.ollama_client import call_llm
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

MAX_POST_CHARS = 240

# Hard cap per pipeline run — most matched coverage never becomes a post.
MAX_POSTS_PER_RUN = 5

# Hard cap per rolling 24h across ALL runs. The per-run cap alone
# multiplied to 480/day at the 15-minute election-season cadence.
MAX_POSTS_PER_DAY = 12

# Minimum spacing between posts about the same race, so five items about
# one busy race can't consume a whole run back-to-back.
RACE_COOLDOWN_HOURS = 6

# Items older than this that were never considered are marked considered
# without posting — news value decays fast, and without a drain the
# not-yet-considered backlog (the posting query is capped per run) would
# grow without bound on busy days (2026-07 review).
CONSIDER_MAX_AGE_HOURS = 48

_SYSTEM_PROMPT = (
    "You are a civic journalist writing brief, factual updates for the Civitas "
    "transparency platform. Civitas aggregates U.S. government and election data "
    "into public scorecards. Your posts are non-partisan, data-grounded, and "
    "written for citizens who want to understand what's happening in their races."
)


def _office_label(race: Race) -> str:
    if race.office == "S":
        label = f"the {race.state} Senate race"
        return f"{label} (special election)" if race.is_special else label
    district = race.district if race.district else "at-large"
    return f"the {race.state}-{district} House race"


def _roster_fact(item: RaceCoverageItem, race: Race, db: Session) -> str | None:
    """The FEC-sourced sentence that grounds this post's race framing.

    Built from the matched candidate's actual roster row — the claim "this
    coverage concerns {race}" is only as good as the name match, which is
    why eligibility requires match_basis == "full_name".

    matched_candidate_id is resolved through the same dedupe rule the
    on-site race page applies (app/candidate_dedup.py): a coverage item
    can be matched against either of two FEC ids for a since-refiled
    candidate, and by the time this posts, the site's own race list may
    show only the surviving one. Posting a claim about an id the site
    doesn't display for this race would be a live, unsupervised
    inconsistency with no human catching it before it ships."""
    if item.matched_candidate_id is None:
        return None
    resolved_id = resolve_candidate_id(item.matched_candidate_id, race.candidates)
    cand = db.query(Candidate).filter(Candidate.id == resolved_id).first()
    if cand is None:
        return None
    return f"FEC filings list {cand.name} as a candidate in {_office_label(race)}."


def _generate_post_text(item: RaceCoverageItem, race: Race, roster_fact: str) -> str | None:
    """Ask the LLM for ONE grounded sentence describing this coverage item.

    Same retry/grounding shape as bluesky_poster._generate_new_post, sized
    down to a single source item instead of an aggregated ActionIssue.
    The race association enters the source material as the explicit
    roster-fact line (see module docstring) — not as unexaminable framing.
    """
    user_prompt = f"""Write ONE brief Bluesky sentence introducing this piece of \
election coverage.

Race: {_office_label(race)}
Candidate on this race's FEC roster: {roster_fact}
Source: {item.source_name}
Title: {item.title}
Content: {item.summary or '(no summary available)'}

RULES — violating any rule means your response is unusable:
1. Use ONLY information from the Race, Candidate, Title, and Content above. \
Do not add details, numbers, or claims not stated there.
2. STRICT MAXIMUM: {MAX_POST_CHARS} characters total.
3. Exactly ONE sentence, ending with proper punctuation.
4. No hashtags, no exclamation points, no editorializing, no "breaking news".
5. Neutral and non-partisan.
6. Report directly — never write "sources say," "reports indicate," or similar.
7. Do not predict or assert a winner, margin, or outcome unless the Content \
explicitly states one.

Return JSON: {{"post": "<your sentence>"}}"""

    source_material = f"{roster_fact}\n{item.title}\n{item.summary or ''}"

    retry_note = ""
    for attempt in range(2):
        result = call_llm(
            prompt_version="election_coverage_post_v2",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt + retry_note,
            model=settings.OLLAMA_STORY_MODEL or None,
            cache_key=None,  # time-sensitive, never cache
            db_session=None,
            max_tokens=200,
            num_ctx=2048,
        )
        if not result or not isinstance(result.get("post"), str):
            return None
        post = strip_hashtags_and_truncate(result["post"], MAX_POST_CHARS)

        reasons = grounding_violations(post, source_material) + hedge_and_editorializing_violations(post)
        if not reasons:
            return post

        logger.warning(
            "Election coverage post failed grounding for item %s (attempt %d): %s | post: %s",
            item.id, attempt + 1, "; ".join(reasons), post[:160],
        )
        retry_note = (
            "\n\nYour previous attempt was rejected because it included "
            f"{'; '.join(reasons)}. Rewrite using only the Race, Candidate, "
            "Title, and Content, report directly instead of hedging "
            "attribution, do not assert any electoral outcome the "
            "Content doesn't state, do not attach a party label to anyone "
            "the Race/Candidate/Title/Content doesn't state one for, and "
            "name specific people by name "
            "instead of a vague indefinite phrase like 'a president' or "
            "'a Speaker' for an office only one person holds at a time."
        )

    return None  # ungrounded twice — skip; a later run can retry


def _publish(text: str, race: Race) -> bool:
    # 2026-08: race detail merged into the state ballot page — old
    # /elections/{race.id} links still redirect here, but new posts go
    # straight to the merged page.
    url = f"https://civitas-research.org/elections/states/{race.state}#race-{race.id}"
    return publish_post(
        text, url,
        success_msg=f"Posted election coverage update: {race.id}",
        error_context=f"race {race.id}",
    )


def _posts_in_last_day(db: Session) -> int:
    since = utcnow() - timedelta(hours=24)
    return (
        db.query(RaceCoverageItem)
        .filter(
            RaceCoverageItem.bsky_posted.is_(True),
            RaceCoverageItem.bsky_posted_at >= since,
        )
        .count()
    )


def _races_posted_recently(db: Session) -> set[str]:
    since = utcnow() - timedelta(hours=RACE_COOLDOWN_HOURS)
    rows = (
        db.query(RaceCoverageItem.race_id)
        .filter(
            RaceCoverageItem.bsky_posted.is_(True),
            RaceCoverageItem.bsky_posted_at >= since,
        )
        .all()
    )
    return {r[0] for r in rows}


def _drain_stale_unconsidered(db: Session) -> int:
    """Mark never-considered items older than CONSIDER_MAX_AGE_HOURS as
    considered-without-posting so the eligible pool stays bounded."""
    cutoff = utcnow() - timedelta(hours=CONSIDER_MAX_AGE_HOURS)
    drained = (
        db.query(RaceCoverageItem)
        .filter(
            RaceCoverageItem.bsky_posted_at.is_(None),
            RaceCoverageItem.fetched_at < cutoff,
        )
        .update({RaceCoverageItem.bsky_posted_at: utcnow()})
    )
    if drained:
        db.commit()
    return drained


def post_race_coverage_updates(db: Session) -> int:
    """Post a capped, prioritized batch of not-yet-considered coverage
    items to Bluesky. No-op if Bluesky credentials aren't configured.
    Every considered item (posted or not) is marked bsky_posted_at so the
    next run doesn't re-evaluate it; actually-published items additionally
    set bsky_posted (the daily budget counts only those).
    """
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return 0

    _drain_stale_unconsidered(db)

    budget = min(MAX_POSTS_PER_RUN, MAX_POSTS_PER_DAY - _posts_in_last_day(db))
    if budget <= 0:
        logger.info("Election coverage posting skipped — daily budget exhausted")
        return 0

    candidates = (
        db.query(RaceCoverageItem)
        .filter(
            RaceCoverageItem.bsky_posted_at.is_(None),
            RaceCoverageItem.match_basis == "full_name",
        )
        .order_by(RaceCoverageItem.fetched_at.desc())
        .limit(MAX_POSTS_PER_RUN)
        .all()
    )
    if not candidates:
        return 0

    races_by_id = {
        r.id: r for r in db.query(Race).filter(
            Race.id.in_({item.race_id for item in candidates}),
        ).all()
    }
    cooled_down = _races_posted_recently(db)

    posted = 0
    for item in candidates:
        race = races_by_id.get(item.race_id)
        # Considered either way — and COMMITTED before any publish attempt:
        # a crash between publish and commit must not re-post the same item
        # on the next run (at-most-once beats at-least-once for a public
        # account; 2026-07 review B3).
        item.bsky_posted_at = utcnow()
        db.commit()
        if race is None:
            continue
        if posted >= budget:
            continue
        if item.race_id in cooled_down:
            logger.info(
                "Skipping post for race %s — posted within the last %dh",
                item.race_id, RACE_COOLDOWN_HOURS,
            )
            continue

        roster_fact = _roster_fact(item, race, db)
        if roster_fact is None:
            continue
        text = _generate_post_text(item, race, roster_fact)
        if not text:
            continue

        if _publish(text, race):
            item.bsky_posted = True
            db.commit()
            cooled_down.add(item.race_id)
            posted += 1

    return posted
