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
        # The two flips over the cap wait for the next hour
        # (test_flips_held_back_by_the_hour_post_in_a_later_pass).
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

    def test_flips_held_back_by_the_hour_post_in_a_later_pass(self, db_session):
        """Throwing away what the budget held back lost the last flips of
        a busy hour for good."""
        for i in range(8):
            _race(db_session, f"2026-HOUSE-GA-{i}", office="H", district=i)
            _event(db_session, f"2026-HOUSE-GA-{i}", er.FLIP)
        assert len(_run(db_session)) == rb.MAX_POSTS_PER_HOUR
        later = utcnow() + timedelta(hours=1, minutes=1)
        with patch.object(rb, "utcnow", return_value=later):
            second = _run(db_session)
        assert len(second) == 2 and all("leads in a seat" in text for text, _ in second)

    def test_a_race_in_its_cooldown_is_held_not_dropped(self, db_session):
        _race(db_session, "2026-SEN-GA")
        _event(db_session, "2026-SEN-GA", er.FLIP, bsky_posted=True, bsky_posted_at=utcnow())
        official = _event(db_session, "2026-SEN-GA", er.OFFICIAL, _detail(reporting=100, official=True))
        assert _run(db_session) == []
        assert official.bsky_posted_at is None
        with patch.object(rb, "utcnow", return_value=utcnow() + timedelta(minutes=rb.RACE_COOLDOWN_MINUTES + 1)):
            [(text, _)] = _run(db_session)
        assert "lists its count as official" in text

    def test_a_failed_publish_is_retried(self, db_session):
        _race(db_session, "2026-SEN-GA")
        e = _event(db_session, "2026-SEN-GA", er.FLIP)
        with patch.object(rb, "publish_post", return_value=False):
            rb.post_result_updates(db_session, DAY)
        assert e.bsky_posted_at is None
        assert len(_run(db_session)) == 1

    def test_a_post_overtakes_its_races_older_lesser_events(self, db_session):
        _race(db_session, "2026-SEN-GA")
        lead = _event(db_session, "2026-SEN-GA", er.LEAD_CHANGE, _detail(), age=timedelta(minutes=5))
        _event(db_session, "2026-SEN-GA", er.FLIP)
        [(text, _)] = _run(db_session)
        assert "leads in a seat" in text
        assert lead.bsky_posted_at is not None and not lead.bsky_posted

    def test_corrections_do_not_spend_the_budget(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        for _ in range(3):
            _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, bsky_posted=True, bsky_posted_at=utcnow())
        assert rb._published_since(db_session, utcnow() - timedelta(hours=1)) == 0

    def test_a_correction_is_owed_past_the_two_hour_cap(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _event(db_session, "2026-SEN-GA", er.FLIP, bsky_posted=True, bsky_posted_at=utcnow() - timedelta(hours=4))
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"), age=timedelta(hours=3))
        [(text, _)] = _run(db_session)
        assert text.startswith("Update on Georgia's U.S. Senate")


class TestComposeFits:
    def _race(self):
        return Race(id="2026-HOUSE-NC-13", office="H", state="NC", district=13, cycle_year=2026)

    def test_long_names_drop_figures_never_the_qualifier(self):
        import json

        d = json.loads(_detail(held="REP", leader_party="DEM"))
        d["leader"]["name"] = "Alexandra Montgomery-Richardson Jr."
        d["runnerUp"]["name"] = "Bartholomew Featherstonehaugh-Smythe"
        text = rb.compose(er.FLIP, self._race(), d)
        assert len(text) <= rb.MAX_POST_CHARS
        assert text.endswith("Not final.")
        assert "reporting" in text

    def test_never_cuts_a_share_mid_number(self):
        import json

        d = json.loads(_detail(held="REP", leader_party="DEM"))
        d["leader"]["name"] = "B" * 65
        d["runnerUp"]["name"] = "Bob Smith"
        text = rb.compose(er.FLIP, self._race(), d)
        assert text.endswith("Not final.") and "49." not in text

    def test_nothing_fits_means_no_post(self):
        import json

        d = json.loads(_detail())
        d["leader"]["name"] = "X" * 300
        assert rb.compose(er.FLIP, self._race(), d) is None

    def test_a_tie_correction_says_tied(self):
        import json

        d = json.loads(_detail())
        d["leader"] = None
        text = rb.compose(rb.CORRECTION, self._race(), d)
        assert text.startswith("Update on North Carolina's 13th Congressional District: the count is now tied")


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
