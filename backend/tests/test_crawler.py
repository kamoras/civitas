"""The crawler page's facts (app/crawler.py, GET /api/crawler) are read from
the code, and the scheduler builds its triggers from the same table."""

import ast
import json
from pathlib import Path

import pytest

from app import contact, crawler
from app.api.crawler import crawler_facts
from app.config import settings

APP = Path(crawler.__file__).resolve().parent


def _readers(host: str) -> dict[str, dict]:
    match = [h for h in crawler.hosts() if h["host"] == host]
    assert match, host
    return {r["module"]: r for r in match[0]["readers"]}


def test_a_limiter_is_read_from_its_source():
    """fec.py's limiter is RateLimiter(settings.FEC_RPS): the page states the
    setting's value, worded."""
    fec = _readers("api.open.fec.gov")["app.pipeline.fetch.fec"]
    assert fec["rps"] == settings.FEC_RPS
    assert fec["pace"] == crawler.pace(settings.FEC_RPS)
    assert _readers("web.archive.org")["app.pipeline.fetch.web_archive"]["rps"] == 0.25


def test_a_reader_without_a_limiter_says_so():
    feeds = _readers("feeds.npr.org")["app.pipeline.fetch.news_feeds"]
    assert feeds["rps"] is None and feeds["pace"] == "no rate limiter of its own"


def test_registry_hosts_are_listed_with_their_registry():
    """The per-state readers keep their addresses in *_sources.json; each
    such host is listed, naming the registry, and its "_" notes are not."""
    registry = json.loads((APP / "data" / "state_candidate_sources.json").read_text())
    walls = set(registry["_robots_walls"]) - {"_about"}
    listed = {h["host"] for h in crawler.hosts()}
    tally = _readers("arkansas.tally-enr.com")
    assert any(r["registry"] == "state_candidate_sources.json" for r in tally.values())
    # A host named only in the notes (a wall's citation) is not a source.
    only_in_notes = {w for w in walls if w not in json.dumps(registry["states"])}
    assert not only_in_notes & listed


def test_links_and_namespaces_are_not_sources():
    listed = {h["host"] for h in crawler.hosts()}
    assert not {"creativecommons.org", "www.w3.org", "schemas.openxmlformats.org"} & listed
    assert settings.SITE_DOMAIN not in listed


def test_photo_hosts_are_listed_under_the_photo_route():
    from app.photos import PHOTO_SOURCE_HOSTS

    for host in set().union(*PHOTO_SOURCE_HOSTS.values()):
        assert crawler.PHOTO_ROUTE in _readers(host)


@pytest.mark.parametrize("fields,words", [
    ({"minute": "15"}, "every hour at :15"),
    ({"minute": "10,40"}, "every hour at :10 and :40"),
    ({"minute": "*/15"}, "every 15 minutes"),
    ({"hour": "*/6", "minute": "50"}, "every 6 hours at :50"),
    ({"hour": "14", "minute": "50"}, "daily at 14:50 UTC"),
    ({"hour": "15-23,0-1", "minute": "40"}, "every hour at :40 from 15:40 to 01:40 UTC"),
    ({"minute": "0", "hour": "3", "day": "*", "month": "*", "day_of_week": "*"}, "daily at 03:00 UTC"),
    ({"minute": "0", "hour": "3", "day_of_week": "mon"}, "minute=0 hour=3 day_of_week=mon"),
])
def test_when(fields, words):
    assert crawler.when(fields) == words


def test_the_scheduler_takes_every_listed_trigger_from_here():
    """Each job the page lists is scheduled from crawler.cron, so the page
    can't state a time the scheduler doesn't keep; and the nightly chain's
    links are the page's, in order."""
    tree = ast.parse((APP / "scheduler.py").read_text())
    cron_ids = [
        n.args[0].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "cron" and getattr(n.func.value, "id", None) == "crawler"
    ]
    assert sorted(cron_ids) == sorted(crawler.SCHEDULE)
    links = [
        n.args[0].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Link"
    ]
    assert tuple(links) == crawler.NIGHTLY_LINKS


def test_the_endpoint_states_the_user_agent_and_signing():
    body = json.loads(crawler_facts().body)
    assert body["userAgent"] == contact.USER_AGENT
    assert body["userAgent"].startswith("Civitas/1.0 (+") and "/about/crawler" in body["userAgent"]
    assert body["contact"] == contact.CONTACT_EMAIL
    assert body["hostCount"] == len(body["hosts"]) > 100
    signing = body["signing"]
    assert signing["configured"] is (signing["directory"] is not None)
    if crawler.web_bot_auth is None:
        assert signing == {"configured": False, "directory": None}


def test_the_shared_client_names_civitas_unless_told_otherwise():
    from app.http_client import make_async_client

    assert make_async_client().headers["User-Agent"] == contact.USER_AGENT
    assert make_async_client(headers={"user-agent": "x"}).headers["User-Agent"] == "x"
    assert make_async_client(headers={"Accept": "a/b"}).headers["User-Agent"] == contact.USER_AGENT
