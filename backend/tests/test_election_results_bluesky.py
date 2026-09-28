"""Election-night Bluesky posts (analyze/election_results_bluesky.py):
what earns a post, the order and budget, and corrections outside it."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.models import ElectionResultEvent, Race, RaceResult
from app.live_results import sync as er
from app.live_results import bluesky as rb
from app.time_utils import utcnow

DAY = "2026-11-03"


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(rb.settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(rb.settings, "BSKY_APP_PASSWORD", "x", raising=False)


def _detail(leader_party="REP", held="DEM", reporting=60, total=100, official=False, **extra):
    import json

    return json.dumps({
        "leader": {"name": "Ray Jones", "party": leader_party, "votes": 1000, "pct": 52.6},
        "runnerUp": {"name": "Dana Smith", "party": "DEM" if leader_party == "REP" else "REP", "votes": 900, "pct": 47.4},
        "votesCounted": 1900, "reportingUnits": reporting, "totalUnits": total, "unitLabel": "precincts",
        "heldBy": held, "official": official, **extra,
    })


def _race(db, rid, office="S", state="GA", district=None, flip=True):
    import json

    db.add(Race(id=rid, cycle_year=2026, office=office, state=state, district=district))
    tallies = [{"name": "Ray Jones", "party": "REP" if flip else "DEM", "votes": 1000},
               {"name": "Dana Smith", "party": "DEM" if flip else "REP", "votes": 900}]
    db.add(RaceResult(race_id=rid, election_date=DAY, source_name="GA SOS", tallies=json.dumps(tallies),
                      votes_counted=1900, reporting_units=60, total_units=100, held_by_party="DEM"))


def _event(db, rid, kind, detail=None, age=timedelta(0), **kw):
    e = ElectionResultEvent(race_id=rid, election_date=DAY, kind=kind, detail=detail or _detail(),
                            created_at=utcnow() - age, **kw)
    db.add(e)
    db.flush()
    return e


def _run(db):
    sent = []

    def fake_publish(text, url, **kw):
        sent.append((text, url))
        return True

    with patch.object(rb, "publish_post", fake_publish):
        rb.post_result_updates(db, DAY)
    return sent


class TestCompose:
    def test_flip_says_leads_and_not_final(self, db_session):
        race = Race(id="2026-SEN-GA", office="S", state="GA", cycle_year=2026)
        import json

        text = rb.compose(er.FLIP, race, json.loads(_detail()))
        assert text == ("Georgia's U.S. Senate: Ray Jones (R) leads in a seat Democrats hold. Ray Jones (R) 52.6%, "
                        "Dana Smith (D) 47.4%. 60 of 100 precincts reporting (60%). Not final.")
        assert len(text) <= rb.MAX_POST_CHARS

    def test_official_flip_says_wins(self):
        import json

        race = Race(id="2026-HOUSE-GA-2", office="H", state="GA", district=2, cycle_year=2026)
        text = rb.compose(er.FLIP, race, json.loads(_detail(official=True)))
        assert text.startswith("Georgia's 2nd Congressional District: Ray Jones (R) wins in the official count")


class TestWhatPosts:
    def test_house_lead_changes_stay_on_the_site(self, db_session):
        _race(db_session, "2026-HOUSE-GA-2", office="H", district=2, flip=False)
        _event(db_session, "2026-HOUSE-GA-2", er.LEAD_CHANGE, _detail(leader_party="DEM"))
        _event(db_session, "2026-HOUSE-GA-2", er.FIRST_RETURNS, _detail(leader_party="DEM"))
        assert _run(db_session) == []

    def test_senate_lead_change_needs_most_of_the_count(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _event(db_session, "2026-SEN-GA", er.LEAD_CHANGE, _detail(leader_party="DEM", reporting=10))
        assert _run(db_session) == []

    def test_old_events_are_history(self, db_session):
        _race(db_session, "2026-SEN-GA")
        e = _event(db_session, "2026-SEN-GA", er.FLIP, age=timedelta(hours=3))
        assert _run(db_session) == []
        assert e.bsky_posted_at is not None  # considered, never again

    def test_a_flip_that_already_reverted_is_not_posted(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _event(db_session, "2026-SEN-GA", er.FLIP)
        assert _run(db_session) == []


class TestBudget:
    def test_flips_go_first_and_the_hour_is_capped(self, db_session):
        for i in range(8):
            _race(db_session, f"2026-HOUSE-GA-{i}", office="H", district=i)
            _event(db_session, f"2026-HOUSE-GA-{i}", er.FLIP)
        _race(db_session, "2026-SEN-GA")
        _event(db_session, "2026-SEN-GA", er.ALL_REPORTING, _detail(reporting=100))
        sent = _run(db_session)
        assert len(sent) == rb.MAX_POSTS_PER_HOUR
        assert all("leads in a seat" in text for text, _ in sent)

    def test_one_post_per_race_per_cooldown(self, db_session):
        _race(db_session, "2026-SEN-GA")
        _event(db_session, "2026-SEN-GA", er.FLIP)
        _event(db_session, "2026-SEN-GA", er.ALL_REPORTING, _detail(reporting=100))
        assert len(_run(db_session)) == 1

    def test_a_correction_posts_even_with_the_budget_spent(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _event(db_session, "2026-SEN-GA", er.FLIP, bsky_posted=True, bsky_posted_at=utcnow())
        for i in range(rb.MAX_POSTS_PER_HOUR):
            _event(db_session, "2026-SEN-GA", er.ALL_REPORTING, bsky_posted=True, bsky_posted_at=utcnow())
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        [(text, url)] = _run(db_session)
        assert text.startswith("Update on Georgia's U.S. Senate: Ray Jones (D) is ahead again")
        assert url.endswith("/elections/states/GA#race-2026-SEN-GA")

    def test_a_reversal_of_an_unposted_flip_is_not_a_correction(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        assert _run(db_session) == []


def test_no_credentials_no_posts(db_session, monkeypatch):
    monkeypatch.setattr(rb.settings, "BSKY_HANDLE", "", raising=False)
    _race(db_session, "2026-SEN-GA")
    _event(db_session, "2026-SEN-GA", er.FLIP)
    assert rb.post_result_updates(db_session, DAY) == 0


def test_counting_is_live_only_while_totals_move(db_session):
    from app.election_phase import ActiveElection, RESULTS

    moving = ActiveElection(date(2026, 11, 3), RESULTS, date(2026, 11, 20), utcnow() - timedelta(hours=2))
    settled = ActiveElection(date(2026, 11, 3), RESULTS, date(2026, 11, 20), utcnow() - timedelta(days=3))
    with patch("app.election_phase.active_election", return_value=moving):
        assert rb.counting_is_live() is True
    with patch("app.election_phase.active_election", return_value=settled):
        assert rb.counting_is_live() is False
