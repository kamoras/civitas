"""GET /api/crawler: what Civitas's crawler is, what it reads, how fast and
when, and how it behaves, for /about/crawler. Every figure is read from the
code (app/crawler.py, app/crawl_policy.py) or, for the hosts refusing it
now, from the record the fetchers keep (crawl_policy.refusals)."""

from datetime import datetime, timezone

from fastapi import APIRouter

from app import crawl_policy, crawler
from app.api.response_helpers import CACHE_TTL_LIST_S, JSONResponse, cached_json

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


def facts() -> dict:
    body = crawler.summary()
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
def crawler_facts() -> JSONResponse:
    # A list's lifetime, not reference data's: the hosts refusing now change.
    return cached_json(facts(), CACHE_TTL_LIST_S)
