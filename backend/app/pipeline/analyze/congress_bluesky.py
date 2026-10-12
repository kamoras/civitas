"""The Congress posts: one per session day, once its record is final, and
one per week, once the week is over and every day of it final. Published
to the Atom feed, and to Bluesky when an account is configured
(app.broadcast.publish).

A day is posted after the Congressional Record's Daily Digest has made
every chamber's row for it final (usually the next evening), so the post
reports the official record rather than a floor log still being written.
The text is the day report's own sentence (congress_service: counts filled
into a fixed template) with the passed bills' numbers, never their titles:
an official short title can read as advocacy ("Protect ... Act"), and the
site does not repeat one under its own name. The link is the day's page.

Only the last few days are eligible, so the Digest back-fill, which fills
in months of past days, can never set off a burst of old posts, and a day
is posted at most once: the published post itself is the marker (its
subject), stored in the same commit that publishes it, and kept through a
data reset.

The weekly post is the week report's sentence (congress_service.week_report)
and the bills that became law that week, by number. It replaced a weekly
recap the local model wrote from the Action Center timeline.

A day or a week in which no record vote was taken and nothing passed or
was confirmed is not posted (has_business). Of the 371 final session days
on record on 2026-10-11, 83 were such days (36 of 156 in 2026, and four of
the seven days posted from 2026-09-24 to 10-09): a pro forma session, or a
few minutes with nothing on the record. "The Senate met in pro forma
session for 0 minutes" told a reader nothing, and posts like it taught
readers to scroll past the account (Congress daily posts: 35 posts, 5
likes, 2026-10-10).

The look-ahead post, once a weekend, says when each chamber next meets
(the Congressional Record's own notice, congress_service.next_meeting) and
which bills passed one chamber the week before and go to the other next
(the week report's passedOneChamber): template text around those, nothing
generated. It is not posted unless the Record's program says some chamber
meets that week for business (_chamber_ahead): replayed over the 92
weekends on record to 2026-10-10, 34 would have posted, and the 58 others
were recesses and weeks of pro forma sessions.
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app import broadcast
from app.models import CongressDay
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS
from app.services.congress_service import (
    CHAMBERS, bill_label, day_report, next_meeting, week_bounds, week_report,
)

logger = logging.getLogger(__name__)

# The markers that recorded a Bluesky post before the feed existed. Read so
# a day posted then isn't posted again, and still written after publishing:
# the image before this one knows a posted day only by these, and Swarm runs
# it again on a rollback (expand, then contract — migrations/README.md).
# Stop writing them in a release after this one.
_CACHE_TIER = "bsky-congress"
_WEEK_CACHE_TIER = "bsky-congress-week"
# A day becomes final the evening after it; two more days of slack cover a
# late Digest. Anything older is history, not news.
_ELIGIBLE_DAYS = 3
# Bill numbers named in a post before it says "and N more".
_MAX_BILLS_NAMED = 4
# The look-ahead names fewer: it carries both chambers' next meetings and
# up to two bill lists in Bluesky's 300 characters, link included.
_MAX_BILLS_AHEAD = 3
# The look-ahead goes out from Saturday (weekday 5): by then the week's
# last session day has written its notice of the next meeting.
_LOOKAHEAD_FROM_WEEKDAY = 5
_CHAMBER_NAME = {"senate": "Senate", "house": "House"}
# The Congress hub: its "this week" strip says when each chamber meets.
_LOOKAHEAD_URL = f"{broadcast.SITE_URL}/congress"
# The counts (congress_service._counts) that say a day did something a post
# can report: a record vote, a measure passed, a nomination confirmed.
_BUSINESS = ("recordVotes", "billsPassed", "resolutionsPassed", "confirmed", "confirmedLists")
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def _day_label(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]}, {_MONTHS[day.month - 1]} {day.day}"


def has_business(counts: list[dict]) -> bool:
    """Whether a day or week (one count dict per chamber) did anything a
    post can report. The rest are pro forma sessions and short sittings
    with nothing on the record."""
    return any(c.get(k) for c in counts for k in _BUSINESS)


def _bill_list(bills: list[str], limit: int = _MAX_BILLS_NAMED) -> str:
    labels = [bill_label(b) for b in bills[:limit]]
    more = len(bills) - len(labels)
    return ", ".join(labels) + (f" and {more} more" if more else "")


def compose_post(report: dict) -> str:
    """The post text for a day report: its date, its sentence, and the
    numbers of the bills each chamber passed."""
    day = date.fromisoformat(report["date"])
    parts = [f"Congress, {_day_label(day)}. {report['sentence']}"]
    for chamber, name in (("senate", "Senate"), ("house", "House")):
        bills = [e["billId"] for e in report["chambers"][chamber]["passed"] if e["billId"] and not e["isResolution"]]
        if not bills:
            continue
        parts.append(f"{name} passed: {_bill_list(bills)}.")
    return " ".join(parts)


def _eligible_days(db: Session, today: date) -> list[date]:
    """Recent days, newest first, on which some chamber met and every
    chamber's record is final."""
    start = (today - timedelta(days=_ELIGIBLE_DAYS)).isoformat()
    rows = db.query(CongressDay).filter(CongressDay.date >= start, CongressDay.date < today.isoformat()).all()
    by_day: dict[str, list[CongressDay]] = {}
    for r in rows:
        by_day.setdefault(r.date, []).append(r)
    out = []
    for iso, day_rows in by_day.items():
        if len(day_rows) < 2 or not all(r.is_final for r in day_rows):
            continue
        if any(r.in_session for r in day_rows):
            out.append(date.fromisoformat(iso))
    return sorted(out, reverse=True)


def page_shows(url: str, sentence: str) -> bool:
    """Whether the page a post links to already says what the post says:
    its card's description (the report sentence, cut to 160 characters
    with "…") is the start of `sentence`.

    The post goes out the moment the Digest makes a day final, and the
    page's cached render (five minutes of data cache, served stale once
    more on the next visit) still showed the record from before it. The
    card and feed summary of the posts for 2026-09-29, 10-01 and 10-05 said
    "No record of the House for this day yet", "No record of the Senate
    for this day yet" and "met for 0 minutes" beside a post saying
    otherwise. Reading the page also starts its re-render, so the next run
    (half an hour on) finds it current. An unreadable page waits too."""
    card = broadcast.fetch_og_card(url)
    if not card:
        return False
    shown = " ".join((card.get("description") or "").split()).rstrip("…").rstrip()
    return bool(shown) and " ".join(sentence.split()).startswith(shown)


def _already_published(db: Session, subject: str, legacy_tier: str, key: str) -> bool:
    if broadcast.was_published(db, subject):
        return True
    return api_cache_get(db, legacy_tier, key, max_age_hours=24 * 30) is not None


def post_daily_congress(db: Session, today: date) -> date | None:
    """Publish the most recent eligible, unpublished day. Returns the day published."""
    for day in _eligible_days(db, today):
        key = day.isoformat()
        url = f"{broadcast.SITE_URL}/congress/{key}"
        subject = f"congress-day:{key}"
        if _already_published(db, subject, _CACHE_TIER, key):
            continue
        report = day_report(db, day)
        if not has_business([report["chambers"][c]["counts"] for c in CHAMBERS]):
            # Re-read every half-hour run while the day is eligible; debug, not info.
            logger.debug("Congress day %s not posted: no record vote, and nothing passed or confirmed", key)
            continue
        if not page_shows(url, report["sentence"]):
            logger.info("Congress post for %s waits: its page doesn't show the final record yet", key)
            return None
        broadcast.publish(db, kind="congress_day", subject=subject, title=f"Congress, {_day_label(day)}",
                          text=compose_post(report), url=url)
        api_cache_set(db, _CACHE_TIER, key, {"posted": True}, normal_ttl_hours=24 * 30)
        return day
    return None


def _week_label(start: date, end: date) -> str:
    first = f"{_MONTHS[start.month - 1]} {start.day}"
    last = str(end.day) if start.month == end.month else f"{_MONTHS[end.month - 1]} {end.day}"
    return f"{first}–{last}"


def compose_week_post(report: dict) -> str:
    """The weekly post: the week, its sentence, and the bills that became law."""
    start, end = date.fromisoformat(report["start"]), date.fromisoformat(report["end"])
    text = f"Congress, week of {_week_label(start, end)}. {report['sentence']}"
    laws = [b["billId"] for b in report["becameLaw"]]
    if laws:
        text += f" Became law: {_bill_list(laws)}."
    return text


def _week_is_final(db: Session, start: date, end: date) -> bool:
    """Some chamber met that week, and every day either met is final."""
    rows = db.query(CongressDay).filter(
        CongressDay.date >= start.isoformat(), CongressDay.date <= end.isoformat(), CongressDay.in_session.is_(True),
    ).all()
    return bool(rows) and all(r.is_final for r in rows)


def post_weekly_congress(db: Session, today: date) -> date | None:
    """Publish last week (Monday to Sunday) once every day of it is final.
    Only the week just ended is eligible, so no backlog is ever posted.
    Returns the week's Monday when published."""
    start, end = week_bounds(today - timedelta(days=7))
    key = start.isoformat()
    url = f"{broadcast.SITE_URL}/congress/week/{key}"
    subject = f"congress-week:{key}"
    if not _week_is_final(db, start, end) or _already_published(db, subject, _WEEK_CACHE_TIER, key):
        return None
    report = week_report(db, start)
    if not has_business(list(report["totals"].values())) and not report["becameLaw"]:
        logger.debug("Congress week of %s not posted: no record vote, and nothing passed or confirmed", key)
        return None
    if not page_shows(url, report["sentence"]):
        logger.info("Congress week post for %s waits: its page doesn't show the final record yet", key)
        return None
    broadcast.publish(db, kind="congress_week", subject=subject, title=f"Congress, week of {_week_label(start, end)}",
                      text=compose_week_post(report), url=url)
    api_cache_set(db, _WEEK_CACHE_TIER, key, {"posted": True}, normal_ttl_hours=24 * 30)
    return start


def _chamber_ahead(chamber: str, when: date | None, program: str, start: date, end: date) -> tuple[str, bool]:
    """One chamber's sentence in the look-ahead, and whether the Record
    says it meets that week for business: a program is printed, and it is
    neither a pro forma session nor "To be announced"."""
    name = _CHAMBER_NAME[chamber]
    if when is None:
        return f"The {name}'s next meeting is not on record yet.", False
    if when > end:
        return f"The {name} is out until {_day_label(when)}.", False
    if when < start:
        # A weekend sitting; the meeting after it isn't on record yet.
        return f"The {name} next meets {_day_label(when)}.", False
    # The Digest's own words ("Senate will meet in a pro forma session",
    # "House will meet in Pro Forma session at 9 a.m.", "Program for Monday:
    # To be announced."), read as congress_service reads a day's
    # adjournment text. No program (a floor log's notice) is not known to
    # be business either: on 2026-10-09 the House's had none, and the House
    # met for two minutes.
    words = program.lower()
    if "pro forma" in words:
        return f"The {name} next meets {_day_label(when)}, in pro forma session.", False
    return f"The {name} next meets {_day_label(when)}.", bool(words) and "to be announced" not in words


def compose_lookahead(start: date, end: date, chambers: dict[str, tuple[date | None, str]],
                      passed_one: list[dict]) -> tuple[str, bool]:
    """The look-ahead's text, and whether either chamber meets that week
    for business. `chambers` is next_meeting's answer per chamber;
    `passed_one` the week report's passedOneChamber for the week before."""
    sentences, business = [], False
    for c in CHAMBERS:
        sentence, meets = _chamber_ahead(c, *chambers[c], start, end)
        sentences.append(sentence)
        business = business or meets
    head = f"Next week in Congress, {_week_label(start, end)}. " + " ".join(sentences)
    # Passed one chamber last week; the other takes it up next.
    waiting = {c: list(dict.fromkeys(e["billId"] for e in passed_one if e.get("nextStep") == c)) for c in CHAMBERS}
    other = {"senate": "House", "house": "Senate"}
    for limit in range(_MAX_BILLS_AHEAD, 0, -1):
        text = head + "".join(
            f" Now before the {_CHAMBER_NAME[c]}, from the {other[c]}: {_bill_list(bills, limit)}."
            for c, bills in waiting.items() if bills
        )
        # Whole on Bluesky, link included, rather than cut there.
        if len(text) + 1 + len(_LOOKAHEAD_URL) <= BSKY_MAX_CHARS:
            break
    return text, business


def post_congress_lookahead(db: Session, today: date) -> date | None:
    """Publish the coming week's look-ahead, once, from the Saturday
    before it. Returns that week's Monday when published."""
    if today.weekday() < _LOOKAHEAD_FROM_WEEKDAY:
        return None
    start = today + timedelta(days=7 - today.weekday())
    end = start + timedelta(days=6)
    subject = f"congress-ahead:{start.isoformat()}"
    if broadcast.was_published(db, subject):
        return None
    chambers = {c: next_meeting(db, c, today) for c in CHAMBERS}
    passed_one = week_report(db, start - timedelta(days=7))["passedOneChamber"]
    text, business = compose_lookahead(start, end, chambers, passed_one)
    if not business:
        return None
    broadcast.publish(db, kind="congress_ahead", subject=subject,
                      title=f"Next week in Congress, {_week_label(start, end)}",
                      text=text, url=_LOOKAHEAD_URL)
    return start
