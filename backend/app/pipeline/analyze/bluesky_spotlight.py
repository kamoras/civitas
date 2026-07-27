"""Periodic Bluesky posts: daily senator score spotlight + weekly civic summary.

Senator spotlight: once per day, picks a senator who hasn't been spotlighted
yet (cycling through all before repeating) and posts a score highlight.

Weekly summary: once per week, condenses the timeline's own record of the week
just ended — the published week-in-review summary, the days behind it, and the
week's dominant policy areas — into a single post.
"""

import json
import logging
import random
import re
from datetime import date, datetime, UTC
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models import BskySenatorSpotlight, Senator, TimelineEntry, WeekSummary
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS, publish_post
from app.pipeline.analyze.grounding import (
    grounding_violations,
    hedge_and_editorializing_violations,
    ungrounded_former_official_claims,
    ungrounded_numbers,
)
from app.pipeline.analyze.ollama_client import call_llm
from app.pipeline.analyze.score_calculator import compute_overall_score

logger = logging.getLogger(__name__)

MAX_SPOTLIGHT_CHARS = 240
SITE = "https://civitas-research.org"

_SYSTEM_PROMPT = (
    "You are a nonpartisan civic journalist writing brief, factual posts for "
    "the Civitas transparency platform. Civitas scores U.S. senators on "
    "funding independence, independent voting, and legislative effectiveness "
    "into an overall representation score. Your posts are data-driven, "
    "neutral, and written to help citizens understand how their representatives "
    "are performing."
)


def _pick_senator(db: Session) -> tuple["Senator | None", int, int]:
    """Return (senator, rank, total), picked uniformly at random from senators
    not yet spotlighted this cycle (cycling through all before repeating).

    Deliberately NOT biased toward the highest or lowest scorer: always
    picking an extreme, combined with the LLM being told to frame it as
    praise or criticism, produced a real incident — a "praise" post about a
    senator's score read as badly out of touch after negative news broke
    about him the same day. A random pick with objective, unevaluative
    framing (see _generate_spotlight_post) can't have the same failure mode.
    """
    spotlighted_ids = {
        row.senator_id
        for row in db.query(BskySenatorSpotlight.senator_id).all()
    }

    senators = (
        db.query(Senator)
        .filter(Senator.score_funding_independence.isnot(None))
        .filter(Senator.is_current.is_(True))
        .all()
    )
    if not senators:
        return None, 0, 0

    # All senators ranked best → worst (for absolute rank lookup)
    all_ranked = sorted(senators, key=compute_overall_score, reverse=True)
    total = len(all_ranked)

    unspotlighted = [s for s in all_ranked if s.id not in spotlighted_ids]
    if not unspotlighted:
        logger.info("All %d senators spotlighted — resetting cycle", total)
        db.query(BskySenatorSpotlight).delete()
        db.commit()
        unspotlighted = list(all_ranked)

    pick = random.choice(unspotlighted)

    rank = next(i + 1 for i, s in enumerate(all_ranked) if s.id == pick.id)
    return pick, rank, total


# Deviation from the neutral midpoint (50) required before a dimension is
# worth singling out by name at all. Below this band a score is unremarkable
# no matter which of the five dimensions happens to be furthest from 50: with
# Promise Persistence's shrinkage prior compressing most senators into the
# low-to-mid 50s (2026-07 audit), the *least-bad* of five middling scores
# was still being singled out ("Her highest score is Promise Persistence at
# 55.0/100" — for a senator ranked 98th of 100). Picking the dimension to
# mention server-side, instead of leaving the choice to the model, removes
# that ambiguity. This is purely about which number gets named — every post,
# notable dimension or not, must still state it as a plain fact (see
# _EMPHASIS_WORDS below, checked unconditionally).
_NOTABLE_DEVIATION = 20

# Evaluative language is banned outright, not just for unremarkable scores —
# posts state numbers as facts and let the reader judge. (A "praise" post
# about a senator's high score read as badly out of touch after negative
# news broke about him hours later; there is no framing that's safe to
# automate, so the fix is not to editorialize at all, ever.)
_EMPHASIS_WORDS = (
    "standout", "impressive", "excel", "shine", "notably", "particularly",
    "remarkable", "exceptional", "outstanding", "strong commitment",
    "effectively", "noteworthy", "concerning", "worrying", "troubling",
    "praiseworthy", "commendable", "questionable", "admirable",
    "disappointing", "impress",
)


def _most_notable_score(scores: dict[str, float]) -> tuple[str, float, bool]:
    """The dimension furthest from neutral, and whether it's actually notable."""
    key = max(scores, key=lambda k: abs(scores[k] - 50))
    value = scores[key]
    return key, value, abs(value - 50) >= _NOTABLE_DEVIATION


def _generate_spotlight_post(senator: "Senator", rank: int, total: int) -> str | None:
    """Ask the LLM to write a score highlight post for this senator."""
    # v6.5: funding diversity folded into funding independence — no longer
    # its own scored dimension (score_funding_independence already reflects
    # it), so it's deliberately not listed here alongside the other three.
    scores = {
        "Funding independence": round(senator.score_funding_independence or 0, 1),
        "Independent voting": round(senator.score_independent_voting or 0, 1),
        "Legislative effectiveness": round(senator.score_legislative_effectiveness or 0, 1),
    }
    # The posted overall must be the same weighted composite the site shows
    # (SCORE_WEIGHTS) — a plain mean of the five dimensions published a
    # different number than the leaderboard for every senator.
    overall = round(compute_overall_score(senator), 1)
    score_lines = "\n".join(f"- {k}: {v}/100" for k, v in scores.items())

    # Rank is stated plainly — never characterized as an achievement or a
    # failure. See module-level note on _pick_senator for why.
    standing = f"ranks #{rank} of {total} senators"

    notable_key, notable_value, is_notable = _most_notable_score(scores)
    if is_notable:
        highlight_instruction = (
            f"2. Name {notable_key} ({notable_value}/100) — it is the score furthest from the "
            f"neutral midpoint (50) for this senator. State the number as a plain fact. Do not "
            f"say it is good, bad, high, low, strong, or weak — just report it."
        )
    else:
        highlight_instruction = (
            "2. None of the five individual scores is unusually far from the neutral midpoint "
            "(50) — they all sit in the ordinary range. Do NOT single out any one dimension. "
            "State the overall score and rank plainly."
        )

    user_prompt = f"""Write a Bluesky post spotlighting this senator's Civitas representation score.

Senator: {senator.name} ({senator.party}-{senator.state})
Overall score: {overall}/100 ({standing})
Individual scores:
{score_lines}

RULES:
1. Mention the senator's name, state, and their overall score and rank.
{highlight_instruction}
3. State every number as a neutral fact. Do not praise, criticize, or use any evaluative
   language — no "good", "bad", "strong", "weak", "impressive", "concerning", or similar.
   This is a report, not a review: let the reader draw their own conclusion from the numbers.
4. STRICT MAXIMUM: {MAX_SPOTLIGHT_CHARS} characters total.
5. Write 1-2 complete sentences ending with proper punctuation.
6. No hashtags, no exclamation points, no editorializing.
7. Do not add any information not provided above.
8. Report directly — never write "sources show," "reports indicate," or similar.
   State the scores as facts, not as something reports/coverage/sources are saying.

Return JSON: {{"post": "<your post text>"}}"""

    retry_note = ""
    for attempt in range(2):
        result = call_llm(
            prompt_version="bsky_spotlight_v2",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt + retry_note,
            cache_key=None,
            db_session=None,
            max_tokens=200,
            num_ctx=1024,
        )
        if not result or not isinstance(result.get("post"), str):
            return None

        text = re.sub(r"#(\w+)", r"\1", result["post"]).strip()
        if len(text) > MAX_SPOTLIGHT_CHARS:
            trimmed = text[:MAX_SPOTLIGHT_CHARS]
            cut = max(
                (trimmed.rfind(p) + 1 for p in (".", "!", "?") if trimmed.rfind(p) > 0),
                default=-1,
            )
            text = trimmed[:cut] if cut > 0 else trimmed[: trimmed.rfind(" ")]
        text = text.strip()

        problems = []

        # Scores publish publicly under the platform's name: every number in
        # the post must be one we actually supplied (a score, the rank, or
        # the senator count). A mangled or invented figure is worse than no
        # post — skip after one corrective retry.
        novel = ungrounded_numbers(text, user_prompt)
        if novel:
            problems.append(f"numbers not provided above ({', '.join(novel)})")

        # Every post must be a neutral report, not a review — no praise or
        # criticism of any score, notable or not. See _EMPHASIS_WORDS.
        lower = text.lower()
        hit = next((w for w in _EMPHASIS_WORDS if w in lower), None)
        if hit:
            problems.append(f'evaluative language ("{hit}") — posts must stay neutral')

        # Same mechanical backstop as the issue poster and full-story
        # generator — prompt-only instructions aren't reliably followed.
        problems += hedge_and_editorializing_violations(text)

        # Stale-training-data status claims ("former Senator X") the
        # supplied scorecard never made — spotlighted senators are sitting
        # members by construction.
        former = ungrounded_former_official_claims(text, user_prompt)
        if former:
            problems.append(
                f"'former' status not in the data provided ({', '.join(former)})"
            )

        if not problems:
            return text
        logger.warning(
            "Spotlight post rejected for %s (attempt %d): %s | post: %s",
            senator.name, attempt + 1, "; ".join(problems), text[:160],
        )
        retry_note = (
            "\n\nYour previous attempt was rejected because it contained "
            f"{' and '.join(problems)}. Use only the scores and ranking "
            "given, and keep the tone plain when no score stands out."
        )

    return None




def _publish_spotlight(text: str, senator: Senator) -> bool:
    """Post the spotlight to Bluesky. Returns True on success."""
    url = f"{SITE}/politicians/{senator.id}"
    return publish_post(
        text, url,
        success_msg=f"Posted senator spotlight: {senator.name}",
        error_context=f"senator {senator.id}",
    )


def post_daily_spotlight(db: Session) -> None:
    """Post a daily senator score spotlight. No-op if already posted today."""
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return

    today = datetime.now(UTC).date().isoformat()
    already_posted = (
        db.query(BskySenatorSpotlight)
        .filter(BskySenatorSpotlight.posted_at >= today)
        .first()
    )
    if already_posted:
        logger.debug("Spotlight already posted today — skipping")
        return

    senator, rank, total = _pick_senator(db)
    if not senator:
        logger.warning("No senators available for spotlight")
        return

    text = _generate_spotlight_post(senator, rank, total)
    if not text:
        logger.warning("Failed to generate spotlight text for %s", senator.name)
        return

    if _publish_spotlight(text, senator):
        db.add(BskySenatorSpotlight(
            senator_id=senator.id,
            posted_at=datetime.now(UTC),
            post_text=text,
        ))
        db.commit()
        logger.info("Senator spotlight posted: %s", senator.name)


# There is no /timeline route — the timeline lives behind a tab on the action
# page, so a bare /timeline link 404s (a user reported one live).
TIMELINE_URL = f"{SITE}/action?tab=timeline"

# Published above every weekly post so a reader meeting it in their feed knows
# it recaps a whole week, not one day's news. Deliberately not "Last week in
# review": post_weekly_summary posts the most recent *summarized* week, and a
# run of days with no timeline entries leaves that week with no WeekSummary
# row at all, so the week being posted is not always the week just gone. The
# parenthetical date range says which week it is; "last" would sometimes lie.
WEEKLY_HEADER = "Week in review ({label}):"
WEEKLY_HEADER_NO_LABEL = "Week in review:"


def _week_label(week: WeekSummary) -> str | None:
    """Human-readable date range for a week: "Jul 13–19", "Jun 29–Jul 5".

    The label is published in the post itself rather than only handed to the
    model, so a week straddling a month boundary has to name both months.

    None when the stored dates don't parse. A raw-range fallback used to be
    fine while the label was prompt-only; published, it would put "  –  " in
    the feed and push the header past the character budget. Callers drop the
    parenthetical instead. strftime is inside the try because "%-d" is a
    glibc extension that raises on other platforms.
    """
    try:
        start = date.fromisoformat(week.start_date)
        end = date.fromisoformat(week.end_date)
        end_format = "%-d" if start.month == end.month else "%b %-d"
        return f"{start.strftime('%b %-d')}–{end.strftime(end_format)}"
    except (TypeError, ValueError):
        return None


def _weekly_header(week: WeekSummary) -> str:
    label = _week_label(week)
    return WEEKLY_HEADER.format(label=label) if label else WEEKLY_HEADER_NO_LABEL


def _weekly_body_budget(header: str) -> int:
    """Characters left for the model's text once the header, the separating
    blank line and the trailing link are spent out of Bluesky's per-post cap.

    Derived rather than hardcoded: a fixed constant silently overflows the
    moment the header or the URL is reworded, and publish_post would then
    truncate the body with nothing failing to warn us.
    """
    return BSKY_MAX_CHARS - len(header) - 1 - len(TIMELINE_URL) - 2


# Per-day detail handed to the model. TimelineEntry.date is unique, so a week
# contributes at most seven days — the whole point of this context is to carry
# the specifics the week summary compressed away, so it gets a far more
# generous per-day budget than action_center's period summarizer, which has to
# fit up to thirty entries into the same context window.
_ENTRY_SUMMARY_CHARS = 240


def _clip(text: str, limit: int) -> str:
    """Trim to at most ``limit`` chars without splitting the final token.

    A mid-token cut is a grounding hole: an entry ending "...a 68-32 vote"
    clipped mid-number leaves "68" in the source, which would then vouch for
    a claim about 68 of something the week never mentioned.
    """
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    cut = trimmed.rfind(" ")
    return (trimmed[:cut] if cut > 0 else trimmed).rstrip()


class _WeekContext(NamedTuple):
    """Two views of a week's timeline record.

    ``prompt`` is everything the model is shown. ``sources`` is the timeline
    text alone — no date brackets, no rule numbers — and is what the model's
    output is checked against. Keeping them apart matters: grounding treats
    its source as a bag of digit tokens, so checking against the whole prompt
    grounded every number printed in it. An ISO date like [2026-07-13] alone
    licenses "7", "13" and "2026", which is how a fabricated "17-13" vote
    tally passed a guard whose entire job is to catch invented figures.
    """
    prompt: str
    sources: str


def _week_entries(week: WeekSummary, db: Session) -> list[TimelineEntry]:
    """The timeline days falling inside the week's date range.

    Guarded on parseable bounds because the range filter is a string
    comparison: an empty start_date is <= every date ever recorded, which
    would sweep years of unrelated days into the prompt labelled "that week."
    """
    try:
        date.fromisoformat(week.start_date)
        date.fromisoformat(week.end_date)
    except (TypeError, ValueError):
        return []

    return (
        db.query(TimelineEntry)
        .filter(TimelineEntry.date >= week.start_date)
        .filter(TimelineEntry.date <= week.end_date)
        .order_by(TimelineEntry.date)
        .all()
    )


def _week_timeline_context(week: WeekSummary, db: Session) -> _WeekContext:
    """The timeline material the platform already holds for this week: the
    published week-in-review summary, the days it was built from, and the
    policy areas the timeline tab shows alongside it.

    The week summary is itself a 2-3 sentence LLM digest of these same days
    (action_center._generate_period_summary), so handing the model only that
    summary made the post a digest of a digest: asked to name specific votes
    and rulings, it was given a text those specifics had been compressed out
    of, and any figure it did recall was then rejected as ungrounded.
    """
    sections: list[str] = []
    sources: list[str] = []

    if week.summary:
        summary = _clip(week.summary, 800)
        sections.append(
            "Week-in-review summary already published on the Civitas timeline:\n"
            f"{summary}"
        )
        sources.append(summary)

    entries = _week_entries(week, db)
    if entries:
        days = []
        for e in entries:
            # Weekday rather than the ISO date: bluesky_poster carries a dated
            # lesson that raw "2026-07-24" phrasing leaks into posts and reads
            # robotic, and a weekday is the natural register for a week recap.
            try:
                weekday = f"[{date.fromisoformat(e.date).strftime('%a')}] "
            except (TypeError, ValueError):
                weekday = ""
            body = _clip(e.summary or "", _ENTRY_SUMMARY_CHARS)
            days.append(f"- {weekday}{e.title}: {body}")
            sources.append(f"{e.title} {body}")
        sections.append(f"The {len(entries)} days that week, as tracked on the timeline:\n" + "\n".join(days))

    try:
        areas = json.loads(week.top_policy_areas or "[]")
    except (TypeError, ValueError):
        areas = []
    if not isinstance(areas, list):
        areas = []  # a JSON scalar would otherwise be joined character by character
    if areas:
        area_text = ", ".join(str(a) for a in areas)
        sections.append(f"Dominant policy areas that week: {area_text}")
        sources.append(area_text)

    return _WeekContext(prompt="\n\n".join(sections), sources="\n".join(sources))


_TIMEFRAME_OPENER_RE = re.compile(r"^(?:last|this|the past)\s+week\b", re.IGNORECASE)


def _weekly_framing_violations(text: str, week: WeekSummary) -> list[str]:
    """Rule 6 as a mechanical check rather than a prompt-only instruction.

    The header already names the week, so a body that opens "Last week…" or
    restates the date range publishes as "Week in review (Jul 13–19): Last
    week, the Senate…". This module treats prompt-only rules as unreliable
    everywhere else it cares about the output (see the hedging and number
    backstops); this one is just as visible and just as cheap to check.
    """
    problems = []
    if _TIMEFRAME_OPENER_RE.match(text.strip()):
        problems.append("a redundant timeframe opener the header already covers")
    label = _week_label(week)
    if label and label in text:
        problems.append(f"the date range the header already shows ({label})")
    return problems


def _generate_weekly_post(week: WeekSummary, db: Session) -> str | None:
    """Condense the week's timeline record into a Bluesky post."""
    header = _weekly_header(week)
    max_chars = _weekly_body_budget(header)

    context = _week_timeline_context(week, db)
    if not context.prompt:
        logger.warning(
            "No timeline material for week %s–%s — skipping weekly post",
            week.start_date, week.end_date,
        )
        return None

    user_prompt = f"""Write a Bluesky post recapping a week in civic news for
Civitas. It stands in for the whole week, so it should read as a summary of the
period rather than a bulletin about one story.

Week: {_week_label(week) or f"{week.start_date} to {week.end_date}"}

{context.prompt}

RULES:
1. 2-3 sentences maximum.
2. STRICT MAXIMUM: {max_chars} characters total.
3. Mention 2-3 of the most significant specific events or developments, drawn
   from across the whole week rather than a single day. Let the week-in-review
   summary set which themes dominated, and take the concrete detail — who acted,
   what passed, what was ruled — from the day entries.
4. No hashtags, no exclamation points, no editorializing.
5. Factual and neutral — report what happened.
6. A header naming the week is printed above your text, so the timeframe is
   already covered. Do not open with "This week" or "Last week", and do not
   restate the dates.
7. Report directly — never write "sources show," "reports indicate," or similar.
   State events as facts, not as something reports/coverage/sources are saying.
8. Do not evaluate whether an action was warranted or justified, and do not
   speculate about its political purpose or effect.

Return JSON: {{"post": "<your post text>"}}"""

    retry_note = ""
    for attempt in range(2):
        result = call_llm(
            prompt_version="bsky_weekly_v1",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt + retry_note,
            cache_key=None,
            db_session=None,
            max_tokens=200,
            num_ctx=2048,
        )
        if not result or not isinstance(result.get("post"), str):
            return None

        text = re.sub(r"#(\w+)", r"\1", result["post"]).strip()
        if len(text) > max_chars:
            trimmed = text[:max_chars]
            cut = max(
                (trimmed.rfind(p) + 1 for p in (".", "!", "?") if trimmed.rfind(p) > 0),
                default=-1,
            )
            text = trimmed[:cut] if cut > 0 else trimmed[: trimmed.rfind(" ")]
        text = text.strip()
        if not text:
            return None

        # Checked against the timeline record alone, not the prompt: grounding
        # reads its source as a bag of digit tokens, so passing the prompt let
        # the model's own rule numbers and any date printed in it vouch for
        # figures nothing in the week actually supports. Same full check and
        # same source-material shape as bluesky_poster and election_bluesky —
        # rule 3 asks for named actors, which is exactly what the wider check
        # (titled names, party, electoral, relationship claims) covers.
        reasons = grounding_violations(text, context.sources)
        reasons += hedge_and_editorializing_violations(text)
        reasons += _weekly_framing_violations(text, week)
        if not reasons:
            # Header goes on after the checks so its date range is never
            # mistaken for something the model has to justify.
            return f"{header} {text}"

        logger.warning(
            "Weekly post failed grounding (attempt %d): %s | post: %s",
            attempt + 1, "; ".join(reasons), text[:160],
        )
        retry_note = (
            "\n\nYour previous attempt was rejected because it contained "
            f"{'; '.join(reasons)}. Use only names and figures from the "
            "week-in-review summary and day entries above, report events "
            "directly instead of through phrases like 'sources show' or "
            "'reports indicate,' leave the timeframe to the header, and do "
            "not evaluate whether any action was warranted or justified."
        )

    return None


def _publish_weekly(text: str, week: WeekSummary) -> bool:
    """Post the weekly summary to Bluesky. Returns True on success."""
    return publish_post(
        text, TIMELINE_URL,
        success_msg=f"Posted weekly summary for week {week.year}/{week.week_num}",
        error_context=f"week {week.year}/{week.week_num}",
    )


def post_weekly_summary(db: Session) -> None:
    """Post the most recently completed week-in-review, at most once per week.

    Older unposted weeks are silently marked as skipped so a backlog of
    unposted summaries doesn't fire in rapid succession after a deployment.
    """
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return

    from datetime import timedelta

    # Enforce a 6-day cooldown — prevents hourly pipeline from posting
    # multiple backlogged weeks in the same day.
    last_posted = (
        db.query(WeekSummary)
        .filter(WeekSummary.bsky_posted_at.isnot(None))
        .order_by(WeekSummary.bsky_posted_at.desc())
        .first()
    )
    if last_posted and last_posted.bsky_posted_at:
        age = datetime.now(UTC) - last_posted.bsky_posted_at.replace(tzinfo=UTC)
        if age < timedelta(days=6):
            logger.debug("Weekly summary posted %d days ago — skipping", age.days)
            return

    unposted = (
        db.query(WeekSummary)
        .filter(WeekSummary.bsky_posted_at.is_(None))
        .order_by(WeekSummary.end_date.desc())
        .all()
    )
    if not unposted:
        return

    # Post only the most recent unposted week; mark the rest as skipped
    week = unposted[0]
    skipped = unposted[1:]
    if skipped:
        for w in skipped:
            w.bsky_posted_at = datetime.now(UTC)  # mark skipped so they don't queue up
        db.commit()
        logger.info("Marked %d stale week summaries as skipped", len(skipped))

    text = _generate_weekly_post(week, db)
    if not text:
        logger.warning("Failed to generate weekly summary post for week %d/%d", week.year, week.week_num)
        return

    if _publish_weekly(text, week):
        week.bsky_posted_at = datetime.now(UTC)
        db.commit()
        logger.info("Weekly summary posted for %s–%s", week.start_date, week.end_date)
