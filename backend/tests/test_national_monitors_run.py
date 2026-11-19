"""National monitors as a whole (action_center._update_national_monitors), on
a test database, across the days a story runs: the day it has run long
enough to open a monitor, the next day's issue on it joining as an update,
and the monitors' lifecycle (watching, closed, deleted).

The embedding model is the member pipelines' stand-in (texts that share
words are near); the local model names nothing, so a monitor opens under
the headline nearest its issues (_representative_issue). A hundred older
issues on other stories make the stored corpus large enough for the
entity half of the matching gate to apply (_MONITOR_ENTITY_MIN_CORPUS).
The rows stored are pinned in fixtures/national_monitors_*_golden.json.
"""

import json
import pathlib
from datetime import date, datetime, timedelta

import pytest

from app.models import ActionIssue, ActionIssueStatus, MonitorStatus, MonitorUpdate, NationalMonitor
from app.pipeline.analyze import action_center as ac
from tests.pipeline_run_helpers import matches_golden, run_on_test_database

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
OPENED = FIXTURES / "national_monitors_opened_golden.json"
NEXT_DAY = FIXTURES / "national_monitors_next_day_golden.json"
LIFECYCLE = FIXTURES / "national_monitors_lifecycle_golden.json"
TODAY = "2026-10-15"
OUTLETS = ["NPR", "PBS", "Roll Call", "Politico", "The Hill"]
PARKS = "Senator Ron Roe presses Congress on national parks maintenance backlog funding"


def _day(offset: int) -> str:
    return (date.fromisoformat(TODAY) + timedelta(days=offset)).isoformat()


def _issue(day: str, title: str, outlet: str, slug: str, **kw) -> ActionIssue:
    return ActionIssue(
        date=day, rank=kw.pop("rank", 1), title=title, summary=f"{title}.",
        source_names=json.dumps([outlet]), source_urls=json.dumps([f"https://{slug}.example.org/{day}"]),
        policy_areas=json.dumps(["ENVIRONMENT"]), status=ActionIssueStatus.CONFIRMED,
        is_current=kw.pop("is_current", False), created_at=datetime.fromisoformat(day) + timedelta(hours=15),
        **kw,
    )


@pytest.fixture()
def monitors(db_session, monkeypatch, freeze_utcnow):
    freeze_utcnow(datetime.fromisoformat(TODAY) + timedelta(hours=16))
    run_on_test_database(monkeypatch, db_session, ac)
    monkeypatch.setattr(ac, "call_llm", lambda **kwargs: None)
    # A hundred older issues on other stories, outside the lookback.
    db_session.add_all(
        _issue("2026-09-01", f"County {n} school board weighs bus contract number {n}", "BBC", f"filler{n}")
        for n in range(100)
    )
    db_session.commit()
    return db_session


def _parks_run(db, last_offset: int) -> None:
    """The parks story on each of the five days before `last_offset`, one
    outlet a day, and today's issue on it."""
    for k, offset in enumerate(range(last_offset - 4, last_offset)):
        outlet = OUTLETS[k % len(OUTLETS)]
        db.add(_issue(_day(offset), PARKS, outlet, outlet.lower().replace(" ", "")))
    db.add(_issue(_day(last_offset), PARKS + " again", "NPR", "npr-today", is_current=True))
    db.commit()


def test_a_story_on_five_days_from_three_outlets_opens_a_monitor(monitors):
    db = monitors
    _parks_run(db, 0)
    ac._update_national_monitors(TODAY, db)
    monitor = db.query(NationalMonitor).one()
    assert monitor.status == MonitorStatus.ACTIVE and monitor.last_article_date == TODAY
    assert monitor.title.startswith("Senator Ron Roe presses Congress")
    assert db.query(MonitorUpdate).filter_by(monitor_id=monitor.id).count() == 5
    assert matches_golden(db, OPENED)


def test_the_next_days_issue_on_the_story_is_an_update(monitors):
    db = monitors
    _parks_run(db, 0)
    ac._update_national_monitors(TODAY, db)
    monitor = db.query(NationalMonitor).one()

    tomorrow = _day(1)
    later = _issue(tomorrow, PARKS + " once more", "PBS", "pbs-tomorrow", is_current=True)
    other = _issue(tomorrow, "Mortgage rates fall for a third straight week", "BBC", "bbc-rates", rank=2)
    db.add_all([later, other])
    db.commit()
    ac._update_national_monitors(tomorrow, db)

    assert db.query(NationalMonitor).count() == 1
    assert db.query(MonitorUpdate).filter_by(monitor_id=monitor.id).count() == 6
    assert json.loads(later.related_monitor_slugs) == [monitor.slug]
    assert json.loads(other.related_monitor_slugs) == []
    assert matches_golden(db, NEXT_DAY)


def test_quiet_monitors_watch_close_and_go(monitors):
    """A week without an update is watching, a month closed, and a closed
    monitor that never gathered three updates is deleted."""
    db = monitors

    def monitor(slug, last_offset, n_updates):
        m = NationalMonitor(slug=slug, title=f"Monitor {slug}", description=f"About {slug}.", category="general",
                            status=MonitorStatus.ACTIVE, policy_areas="[]", last_article_date=_day(last_offset))
        db.add(m)
        db.commit()
        db.add_all(
            MonitorUpdate(monitor_id=m.id, date=_day(last_offset - k), summary=f"{slug} {k}.",
                          source_url=f"https://{slug}.example.org/{k}", source_name="NPR",
                          article_title=f"{slug} {k}")
            for k in range(n_updates)
        )
        db.commit()

    monitor("fresh", -2, 3)
    monitor("quiet", -8, 3)
    monitor("over", -31, 3)
    monitor("thin", -31, 2)
    ac._update_national_monitors(TODAY, db)  # a quiet day: no issue today

    status = {m.slug: m.status for m in db.query(NationalMonitor)}
    assert status == {"fresh": MonitorStatus.ACTIVE, "quiet": MonitorStatus.WATCHING, "over": MonitorStatus.CLOSED}
    assert matches_golden(db, LIFECYCLE)


def test_merging_two_monitors_keeps_every_update(monitors):
    """Two monitors on one story merge into the one with more updates, and
    the other's updates move to it: none is lost to the deleted monitor."""
    db = monitors

    def monitor(slug, title, n_updates):
        m = NationalMonitor(slug=slug, title=title, description=f"{title}.", category="general",
                            status=MonitorStatus.ACTIVE, policy_areas="[]", last_article_date=_day(-1))
        db.add(m)
        db.commit()
        db.add_all(
            MonitorUpdate(monitor_id=m.id, date=_day(-k), summary=f"{slug} {k}.",
                          source_url=f"https://{slug}.example.org/{k}", source_name="NPR",
                          article_title=f"{title} {k}")
            for k in range(1, n_updates + 1)
        )
        db.commit()

    monitor("parks", "National parks maintenance backlog funding", 3)
    monitor("parks-two", "National parks maintenance backlog funding fight", 2)
    db.add(_issue(TODAY, "Mortgage rates fall for a third straight week", "BBC", "bbc-rates", is_current=True))
    db.commit()
    ac._update_national_monitors(TODAY, db)

    kept = db.query(NationalMonitor).one()
    assert kept.slug == "parks"
    assert sorted(u.source_url for u in db.query(MonitorUpdate)) == sorted(
        [f"https://parks.example.org/{k}" for k in (1, 2, 3)] + [f"https://parks-two.example.org/{k}" for k in (1, 2)]
    )
