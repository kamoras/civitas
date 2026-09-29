"""Election-night posts (live_results/bluesky.py), published to the feed
and Bluesky: what earns a post, the order and budget, corrections outside
it, and what a refused send or a data reset does."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app import broadcast
from app.models import BroadcastPost, ElectionResultEvent, Race, RaceResult
from app.live_results import sync as er
from app.live_results import bluesky as rb
from app.time_utils import utcnow

DAY = "2026-11-03"


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    monkeypatch.setattr(broadcast.settings, "BSKY_HANDLE", "civitas.test", raising=False)
    monkeypatch.setattr(broadcast.settings, "BSKY_APP_PASSWORD", "x", raising=False)


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


def _said(db, rid, kind, at=None, status="sent"):
    """A result post already published — the history the pass reads."""
    db.add(BroadcastPost(kind="result", subject=rb._subject(DAY, rid, kind), title="t", text="t",
                         url="https://civitas-research.org/x", state=rid.split("-")[-1][:2],
                         published_at=at or utcnow(), bsky_status=status))
    db.flush()


def _run(db):
    sent = []

    def fake_publish(text, url, **kw):
        sent.append((text, url))
        return True

    with patch.object(broadcast, "publish_post", fake_publish):
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
        _said(db_session, "2026-SEN-GA", er.FLIP)
        for i in range(rb.MAX_POSTS_PER_HOUR):
            _said(db_session, "2026-SEN-GA", er.ALL_REPORTING)
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
        _said(db_session, "2026-SEN-GA", er.FLIP)
        official = _event(db_session, "2026-SEN-GA", er.OFFICIAL, _detail(reporting=100, official=True))
        assert _run(db_session) == []
        assert official.bsky_posted_at is None
        with patch.object(rb, "utcnow", return_value=utcnow() + timedelta(minutes=rb.RACE_COOLDOWN_MINUTES + 1)):
            [(text, _)] = _run(db_session)
        assert "lists its count as official" in text

    def test_a_refused_send_is_in_the_feed_and_never_resent(self, db_session):
        """Bluesky refusing a send doesn't hold the post back from the feed.
        It is not resent (test_a_refused_flip_is_never_resent_after_its_correction)."""
        _race(db_session, "2026-SEN-GA")
        e = _event(db_session, "2026-SEN-GA", er.FLIP)
        with patch.object(broadcast, "publish_post", return_value=False):
            assert rb.post_result_updates(db_session, DAY) == 1
        assert e.bsky_posted is True
        [post] = db_session.query(BroadcastPost).all()
        assert (post.kind, post.subject, post.state, post.bsky_status) == (
            "result", f"result:{DAY}:2026-SEN-GA:flip", "GA", "failed")
        assert "leads in a seat" in post.text
        assert _run(db_session) == []  # this pass won't send it again

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
            _said(db_session, "2026-SEN-GA", rb.CORRECTION)
        h = rb._history(db_session, DAY, utcnow())
        assert (h.last_hour, h.this_election) == (0, 0)

    def test_a_correction_is_owed_past_the_two_hour_cap(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _said(db_session, "2026-SEN-GA", er.FLIP, at=utcnow() - timedelta(hours=4))
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


def test_without_bluesky_the_post_still_goes_to_the_feed(db_session, monkeypatch):
    monkeypatch.setattr(broadcast.settings, "BSKY_HANDLE", "", raising=False)
    _race(db_session, "2026-SEN-GA")
    _event(db_session, "2026-SEN-GA", er.FLIP)
    with patch.object(broadcast, "publish_post") as send:
        assert rb.post_result_updates(db_session, DAY) == 1
    send.assert_not_called()
    [post] = db_session.query(BroadcastPost).all()
    assert (post.kind, post.bsky_status) == ("result", "off")
    assert post.url.endswith("/elections/states/GA#race-2026-SEN-GA")


def test_result_posts_are_in_the_elections_feed_and_not_the_race_budget():
    """The routine race-coverage poster counts `race` posts against its own
    daily budget; election-night posts are their own kind."""
    assert "result" in broadcast.FEEDS["elections"].kinds
    assert "result" in broadcast.FEEDS["all"].kinds


def test_counting_is_live_only_while_totals_move(db_session):
    from app.election_phase import ActiveElection, RESULTS

    moving = ActiveElection(date(2026, 11, 3), RESULTS, date(2026, 11, 20), utcnow() - timedelta(hours=2))
    settled = ActiveElection(date(2026, 11, 3), RESULTS, date(2026, 11, 20), utcnow() - timedelta(days=3))
    with patch("app.election_phase.active_election", return_value=moving):
        assert rb.counting_is_live() is True
    with patch("app.election_phase.active_election", return_value=settled):
        assert rb.counting_is_live() is False


class TestRoundTwo:
    def test_a_refused_send_does_not_hold_the_rest_back_from_the_feed(self, db_session):
        """Each send is one login, once; the budget bounds a pass, and the
        hourly retry (not this pass) tries the refused ones again."""
        calls = []
        for i in range(3):
            _race(db_session, f"2026-HOUSE-GA-{i}", office="H", district=i)
            _event(db_session, f"2026-HOUSE-GA-{i}", er.FLIP)
        with patch.object(broadcast, "publish_post", side_effect=lambda *a, **k: calls.append(a) or False):
            assert rb.post_result_updates(db_session, DAY) == 3
        assert len(calls) == 3
        assert db_session.query(BroadcastPost).filter_by(kind="result").count() == 3

    def test_a_published_post_survives_a_later_failure_in_the_pass(self, db_session):
        _race(db_session, "2026-HOUSE-GA-1", office="H", district=1)
        _race(db_session, "2026-HOUSE-GA-2", office="H", district=2)
        first = _event(db_session, "2026-HOUSE-GA-1", er.FLIP, age=timedelta(minutes=1))
        _event(db_session, "2026-HOUSE-GA-2", er.FLIP)
        sent = []

        def publish(text, url, **kw):
            if sent:
                raise RuntimeError("boom")
            sent.append(text)
            return True

        with patch.object(broadcast, "publish_post", publish), pytest.raises(RuntimeError):
            rb.post_result_updates(db_session, DAY)
        db_session.rollback()
        db_session.refresh(first)
        assert first.bsky_posted is True  # never sent twice

    def test_a_correction_is_dropped_once_the_flip_is_back(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=True)
        _said(db_session, "2026-SEN-GA", er.FLIP, at=utcnow() - timedelta(hours=1))
        correction = _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        assert _run(db_session) == []
        assert correction.bsky_posted_at is not None and not correction.bsky_posted

    def test_a_post_says_where_the_count_stands_now(self, db_session):
        """Held behind the budget, the event's own figures can be two
        hours old; the post uses the stored count."""
        import json

        _race(db_session, "2026-SEN-GA")
        db_session.flush()
        result = db_session.get(RaceResult, "2026-SEN-GA")
        result.official = True
        result.reporting_units = 100
        result.tallies = json.dumps([{"name": "Ray Jones", "party": "REP", "votes": 1200},
                                     {"name": "Dana Smith", "party": "DEM", "votes": 800}])
        result.votes_counted = 2000
        _event(db_session, "2026-SEN-GA", er.FLIP, _detail(reporting=60))
        [(text, _)] = _run(db_session)
        assert "wins in the official count" in text and "60.0%" in text and "Not final" not in text


class TestRoundThree:
    def test_the_link_never_costs_the_qualifier(self, db_session):
        """publish_post appends the link and cuts anything over 300 at a
        sentence boundary — which is "Not final."."""
        from app.pipeline.analyze import bluesky_utils

        _race(db_session, "2026-HOUSE-GA-14", office="H", district=14)
        e = _event(db_session, "2026-HOUSE-GA-14", er.FLIP)
        import json

        result = db_session.get(RaceResult, "2026-HOUSE-GA-14")
        tallies = json.loads(result.tallies)
        tallies[0]["name"] = "Bartholomew Featherstonehaugh-Smythe"
        tallies[1]["name"] = "Alexandra Montgomery-Richardson"
        result.tallies = json.dumps(tallies)
        result.reporting_units, result.total_units = 1850, 2600
        db_session.flush()
        [(text, url)] = _run(db_session)
        assert len(text) + 1 + len(url) <= bluesky_utils.BSKY_MAX_CHARS
        assert text.endswith("Not final.")
        assert e.bsky_posted

    def test_no_second_correction_while_the_last_word_is_one(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        t = utcnow()
        _said(db_session, "2026-SEN-GA", er.FLIP, at=t - timedelta(minutes=30))
        _said(db_session, "2026-SEN-GA", rb.CORRECTION, at=t - timedelta(minutes=20))
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        assert _run(db_session) == []

    def test_all_reporting_without_units_says_nothing(self):
        import json

        race = Race(id="2026-SEN-GA", office="S", state="GA", cycle_year=2026)
        d = json.loads(_detail())
        d["totalUnits"] = None
        assert rb.compose(er.ALL_REPORTING, race, d) is None


class TestPublishing:
    def test_a_refused_flip_is_never_resent_after_its_correction(self, db_session):
        """The hourly retry resent a refused flip word for word — after the
        count had reverted and the correction had gone out, leaving the
        false flip as the account's last word on the race."""
        _race(db_session, "2026-SEN-GA")
        _event(db_session, "2026-SEN-GA", er.FLIP)
        with patch.object(broadcast, "publish_post", return_value=False):
            rb.post_result_updates(db_session, DAY)
        result = db_session.get(RaceResult, "2026-SEN-GA")
        import json

        result.tallies = json.dumps([{"name": "Dana Smith", "party": "DEM", "votes": 1100},
                                     {"name": "Ray Jones", "party": "REP", "votes": 1000}])
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        later = utcnow() + timedelta(minutes=rb.RACE_COOLDOWN_MINUTES + 1)
        with patch.object(rb, "utcnow", return_value=later):
            _run(db_session)
        with patch.object(broadcast, "utcnow", return_value=utcnow() + timedelta(hours=2)):
            sent = []
            with patch.object(broadcast, "publish_post", lambda text, url, **k: sent.append(text) or True):
                broadcast.deliver_pending(db_session)
        assert sent == []

    def test_a_correction_of_a_flip_bluesky_never_showed_stays_in_the_feed(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _said(db_session, "2026-SEN-GA", er.FLIP, at=utcnow() - timedelta(hours=1), status="failed")
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        assert _run(db_session) == []  # nothing sent to Bluesky
        correction = db_session.query(BroadcastPost).filter(
            BroadcastPost.subject == rb._subject(DAY, "2026-SEN-GA", rb.CORRECTION)).one()
        assert correction.bsky_status == "off"

    def test_a_correction_of_a_flip_bluesky_showed_goes_to_bluesky(self, db_session):
        _race(db_session, "2026-SEN-GA", flip=False)
        _said(db_session, "2026-SEN-GA", er.FLIP, at=utcnow() - timedelta(hours=1), status="sent")
        _event(db_session, "2026-SEN-GA", er.FLIP_REVERSED, _detail(leader_party="DEM"))
        [(text, _)] = _run(db_session)
        assert "no longer shows a change of party" in text

    def test_an_official_flip_is_titled_official(self, db_session):
        _race(db_session, "2026-SEN-GA")
        db_session.flush()
        db_session.get(RaceResult, "2026-SEN-GA").official = True
        _event(db_session, "2026-SEN-GA", er.FLIP, _detail(official=True))
        [(text, _)] = _run(db_session)
        [post] = db_session.query(BroadcastPost).all()
        assert "wins in the official count" in text
        assert "not final" not in post.title and "official count" in post.title

    def test_a_data_reset_does_not_post_the_night_again(self, db_session):
        """broadcast_posts survives a reset; the events don't. The rebuilt
        count raises the same flip and official count again."""
        _race(db_session, "2026-SEN-GA")
        _said(db_session, "2026-SEN-GA", er.FLIP, at=utcnow() - timedelta(hours=1))
        _said(db_session, "2026-SEN-GA", er.OFFICIAL, at=utcnow() - timedelta(minutes=50))
        flip = _event(db_session, "2026-SEN-GA", er.FLIP)
        official = _event(db_session, "2026-SEN-GA", er.OFFICIAL, _detail(reporting=100, official=True))
        assert _run(db_session) == []
        assert flip.bsky_posted_at is not None and not flip.bsky_posted
        assert official.bsky_posted_at is not None and not official.bsky_posted

    def test_the_budget_survives_a_data_reset(self, db_session):
        for i in range(rb.MAX_POSTS_PER_HOUR):
            _said(db_session, f"2026-HOUSE-GA-{i}", er.FLIP, at=utcnow() - timedelta(minutes=30))
        _race(db_session, "2026-HOUSE-GA-9", office="H", district=9)
        _event(db_session, "2026-HOUSE-GA-9", er.FLIP)
        assert _run(db_session) == []

    def test_a_runoff_has_its_own_budget(self, db_session):
        db_session.add(BroadcastPost(kind="result", subject=rb._subject("2026-12-01", "2026-SEN-GA", er.FLIP),
                                     title="t", text="t", url="u", published_at=utcnow(), bsky_status="sent"))
        db_session.flush()
        assert rb._history(db_session, DAY, utcnow()).this_election == 0
