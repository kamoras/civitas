"""The Congress posts on Bluesky: one per session day, once its record is
final, and one per week, once the week is over and every day of it final.

A day is posted after the Congressional Record's Daily Digest has made
every chamber's row for it final (usually the next evening), so the post
reports the official record rather than a floor log still being written.
The text is the day report's own sentence (congress_service: counts filled
into a fixed template) with the passed bills' numbers, never their titles:
an official short title can read as advocacy ("Protect ... Act"), and the
site does not repeat one under its own name. The link is the day's page.

Only the last few days are eligible, so the Digest back-fill, which fills
in months of past days, can never set off a burst of old posts, and a day
is posted at most once (a marker in api_cache, written only on success).

The weekly post is the week report's sentence (congress_service.week_report)
and the bills that became law that week, by number. It replaced a weekly
recap the local model wrote from the Action Center timeline.
"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CongressDay
from app.pipeline.analyze.bluesky_utils import publish_post
from app.pipeline.cache import api_cache_get, api_cache_set
from app.services.congress_service import bill_label, day_report, week_bounds, week_report

logger = logging.getLogger(__name__)

SITE = "https://civitas-research.org"
_CACHE_TIER = "bsky-congress"
_WEEK_CACHE_TIER = "bsky-congress-week"
# A day becomes final the evening after it; two more days of slack cover a
# late Digest. Anything older is history, not news.
_ELIGIBLE_DAYS = 3
# Bill numbers named in a post before it says "and N more".
_MAX_BILLS_NAMED = 4
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]


def _day_label(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]}, {_MONTHS[day.month - 1]} {day.day}"


def _bill_list(bills: list[str]) -> str:
    labels = [bill_label(b) for b in bills[:_MAX_BILLS_NAMED]]
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


def post_daily_congress(db: Session, today: date) -> date | None:
    """Post the most recent eligible, unposted day. Returns the day posted."""
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return None
    for day in _eligible_days(db, today):
        key = day.isoformat()
        if api_cache_get(db, _CACHE_TIER, key, max_age_hours=24 * 30):
            continue
        report = day_report(db, day)
        url = f"{SITE}/congress/{key}"
        if not publish_post(compose_post(report), url, success_msg=f"Posted Congress day {key}",
                            error_context=f"Congress day {key}"):
            return None  # tried and failed: the next run tries again
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
    """Post last week (Monday to Sunday) once every day of it is final.
    Only the week just ended is eligible, so no backlog is ever posted.
    Returns the week's Monday when posted."""
    if not getattr(settings, "BSKY_HANDLE", "") or not getattr(settings, "BSKY_APP_PASSWORD", ""):
        return None
    start, end = week_bounds(today - timedelta(days=7))
    key = start.isoformat()
    if not _week_is_final(db, start, end) or api_cache_get(db, _WEEK_CACHE_TIER, key, max_age_hours=24 * 30):
        return None
    report = week_report(db, start)
    if not publish_post(compose_week_post(report), f"{SITE}/congress/week/{key}",
                        success_msg=f"Posted Congress week {key}", error_context=f"Congress week {key}"):
        return None
    api_cache_set(db, _WEEK_CACHE_TIER, key, {"posted": True}, normal_ttl_hours=24 * 30)
    return start
