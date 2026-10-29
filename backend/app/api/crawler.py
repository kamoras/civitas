"""GET /api/crawler: what Civitas's crawler is, what it reads, how fast and
when, and how it behaves, for /about/crawler. Every figure is read from the
code (app/crawler.py, app/crawl_policy.py) or, for the hosts refusing it
now, from the record the fetchers keep (crawl_policy.refusals)."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import crawl_policy, crawler
from app.api.response_helpers import CACHE_TTL_LIST_S, JSONResponse, cached_json
from app.database import get_db
from app.host_terms_check import last_check

router = APIRouter()


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="minutes")


def _seconds(s: float) -> str:
    """"30 minutes", "7 days", "2 minutes", "1 second"."""
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        if s >= size and s % size == 0:
            n = int(s // size)
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{round(s, 2):g} seconds"


def facts(db: Session | None = None) -> dict:
    body = crawler.summary()
    for t in body["termsHosts"]:
        # When the weekly check last found the terms as reviewed (the
        # Internet Archive's capture date), else when they were read.
        record = last_check(db, t["host"]) if isinstance(db, Session) else None
        day = date.fromisoformat((record or {}).get("confirmedOn") or t["readOn"])
        t["confirmedOn"] = f"{day:%b} {day.day}, {day.year}"
    for h in body["hosts"]:
        h["minimumGap"] = _seconds(crawl_policy.interval(h["host"]))
    body["manners"] = {
        "minimumGap": _seconds(crawl_policy.DEFAULT_INTERVAL_S),
        "longestWait": _seconds(crawl_policy.MAX_WAIT_S),
        "firstBackoff": _seconds(crawl_policy.REFUSAL_BASE_S),
        "longestBackoff": _seconds(crawl_policy.REFUSAL_MAX_S),
        "robotsKept": _seconds(crawl_policy.ROBOTS_TTL_S),
    }
    body["refusing"] = [
        {"host": host, "reason": e.get("reason"), "since": e.get("since"),
         "times": e.get("count"), "until": _when(e["until"])}
        for host, e in crawl_policy.refusals().items()
    ]
    return body


@router.get("/crawler")
def crawler_facts(db: Session = Depends(get_db)) -> JSONResponse:
    # A list's lifetime, not reference data's: the hosts refusing now change.
    return cached_json(facts(db), CACHE_TTL_LIST_S)
