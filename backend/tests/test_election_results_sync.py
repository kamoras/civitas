"""The live-results sync (live_results/sync.py) and the seat-flip
DEVELOPING issue it opens (live_results/signals.py)."""

import asyncio
import json
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from app.models import ActionIssue, ActionIssueStatus, Candidate, ElectionResultEvent, Race, RaceResult, Representative, Senator
from app.live_results import sync as er
from app.live_results import signals
from app.pipeline.fetch.election_results import ContestCount, StateCount
from app.time_utils import utcnow

DAY = date(2026, 11, 3)


def _state(**kw):
    return StateCount(source_name=kw.pop("source_name", "Georgia Secretary of State"),
                      page_url="https://results.example/ga", official=kw.pop("official", False), **kw)


def _contest(d, r, reporting, total=100, office="H", district=2, others=()):
    return ContestCount(
        office=office, district=district,
        candidates=[("Dana Smith", "D", d), ("Ray Jones", "R", r), *others],
        reporting_units=reporting, total_units=total,
    )


def _setup(db, held_by="D"):
    race = Race(id="2026-HOUSE-GA-2", cycle_year=2026, office="H", state="GA", district=2)
    db.add(race)
    db.add(Candidate(id="H6GA02001", race_id=race.id, name="SMITH, DANA", party="DEM", incumbent_challenge="I"))
    db.add(Candidate(id="H6GA02002", race_id=race.id, name="JONES, RAY", party="REP"))
    db.add(Representative(id="S000001", name="Dana Smith", state="GA", district=2, party=held_by))
    db.flush()
    return race


def _apply(db, race, contest, **state_kw):
    """As sync_state does it: store, then hand every unheld result on."""
    applied = er.apply_count(db, race, contest, _state(**state_kw), DAY)
    db.flush()
    if not applied.held:
        signals.update_developing_issues(db, [applied])
        db.flush()
    return [e.kind for e in applied.events], applied.result


def _issues(db):
    return db.query(ActionIssue).filter(ActionIssue.source_type == signals.SOURCE_TYPE).all()


class TestApplyCount:
    def test_first_returns_match_candidates_and_freeze_the_holder(self, db_session):
        race = _setup(db_session)
        kinds, result = _apply(db_session, race, _contest(100, 90, 5))
        assert kinds == [er.FIRST_RETURNS]
        tallies = json.loads(result.tallies)
        assert [(t["candidateId"], t["party"]) for t in tallies] == [("H6GA02001", "DEM"), ("H6GA02002", "REP")]
        assert result.held_by_party == "DEM"
        assert result.votes_counted == 190

    def test_nothing_changed_is_no_event_and_no_new_change_time(self, db_session):
        race = _setup(db_session)
        _, result = _apply(db_session, race, _contest(100, 90, 5))
        stamped = result.last_change_at
        kinds, result = _apply(db_session, race, _contest(100, 90, 5))
        assert kinds == []
        assert result.last_change_at == stamped

    def test_a_new_leader_is_a_lead_change(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(100, 90, 5))
        kinds, _ = _apply(db_session, race, _contest(100, 140, 20))
        assert kinds == [er.LEAD_CHANGE]
        detail = json.loads(db_session.query(ElectionResultEvent).filter_by(kind=er.LEAD_CHANGE).one().detail)
        assert detail["leader"]["name"] == "Ray Jones"
        assert detail["previousLeader"]["name"] == "Dana Smith"

    def test_every_unit_in_and_the_source_calling_it_official(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(100, 90, 50))
        kinds, _ = _apply(db_session, race, _contest(200, 150, 100))
        assert kinds == [er.ALL_REPORTING]
        kinds, _ = _apply(db_session, race, _contest(200, 150, 100), official=True)
        assert kinds == [er.OFFICIAL]

    def test_an_exact_tie_has_no_leader(self, db_session):
        race = _setup(db_session)
        _, result = _apply(db_session, race, _contest(100, 100, 50))
        assert er.event_detail(result)["leader"] is None


class TestFlip:
    def test_a_flip_on_a_sliver_of_the_count_is_not_reported(self, db_session):
        race = _setup(db_session)
        kinds, _ = _apply(db_session, race, _contest(90, 100, 10))
        assert er.FLIP not in kinds
        assert _issues(db_session) == []

    def test_flip_with_most_of_the_count_in_opens_a_templated_developing_issue(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(90, 100, 10))
        kinds, result = _apply(db_session, race, _contest(900, 1000, 60))
        assert er.FLIP in kinds
        [issue] = _issues(db_session)
        assert issue.status == ActionIssueStatus.DEVELOPING
        assert issue.is_current and issue.rank == 999
        assert issue.title == "Republican leads Georgia's 2nd Congressional District count in a seat Democrats hold"
        facts = json.loads(issue.facts)
        assert facts[0] == "Ray Jones (R): 1,000 votes, 52.6%"
        assert "60 of 100 precincts reporting (60%)" in facts
        assert facts[-1] == "The seat is held by a Democrat going into this election"
        assert json.loads(issue.actions)[0]["url"] == "/elections/states/GA#race-2026-HOUSE-GA-2"
        assert issue.confirmation_deadline > utcnow()
        assert result.developing_issue_id == issue.id

    def test_the_issue_follows_the_count_and_retires_if_the_lead_reverts(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        _apply(db_session, race, _contest(1500, 1600, 80))
        [issue] = _issues(db_session)
        assert json.loads(issue.facts)[0] == "Ray Jones (R): 1,600 votes, 51.6%"
        assert json.loads(issue.previous_facts)[0] == "Ray Jones (R): 1,000 votes, 52.6%"
        kinds, _ = _apply(db_session, race, _contest(1800, 1700, 90))
        assert er.FLIP_REVERSED in kinds
        assert issue.is_current is False
        # The flip coming back is a new story: a fresh issue (the refresh
        # retires an unmatched developing row a day after it was created,
        # so reviving the old one didn't last), the old one kept as the
        # record of the reversal.
        _apply(db_session, race, _contest(1800, 1900, 95))
        old, new = sorted(_issues(db_session), key=lambda i: i.id)
        assert old is issue and old.is_current is False and "no longer shows" in old.title
        assert new.is_current is True and "leads" in new.title
        assert db_session.get(RaceResult, race.id).developing_issue_id == new.id

    def test_an_expired_issue_is_not_resurrected(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        _apply(db_session, race, _contest(1800, 1700, 90))
        [issue] = _issues(db_session)
        issue.confirmation_deadline = utcnow() - timedelta(hours=1)
        _apply(db_session, race, _contest(1800, 1900, 95))
        assert issue.is_current is False
        assert len(_issues(db_session)) == 2  # the new flip is its own issue

    def test_a_promoted_issue_is_left_to_the_news(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        issue.status = ActionIssueStatus.CONFIRMED
        issue.title = "From the press"
        _apply(db_session, race, _contest(1800, 1700, 90))
        assert issue.title == "From the press" and issue.is_current is True

    def test_official_count_says_wins(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 100), official=True)
        [issue] = _issues(db_session)
        assert issue.title.startswith("Republican wins Georgia's 2nd Congressional District in the official count")

    def test_no_known_holder_is_no_flip(self, db_session):
        race = _setup(db_session)
        db_session.query(Representative).delete()
        _apply(db_session, race, _contest(900, 1000, 60))
        assert _issues(db_session) == []


class TestSenateHolder:
    def test_open_seat_with_split_delegation_is_unknown(self, db_session):
        race = Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA")
        db_session.add(race)
        db_session.add(Senator(id="A", name="Ann Alpha", state="GA", party="D"))
        db_session.add(Senator(id="B", name="Bob Beta", state="GA", party="R"))
        db_session.flush()
        assert er.seat_holder_party(db_session, race) is None

    def test_incumbent_candidate_names_the_seat(self, db_session):
        race = Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA")
        db_session.add(race)
        db_session.add(Candidate(id="S1", race_id=race.id, name="BETA, BOB", party="REP", incumbent_challenge="I"))
        db_session.add(Senator(id="A", name="Ann Alpha", state="GA", party="D"))
        db_session.add(Senator(id="B", name="Bob Beta", state="GA", party="R"))
        db_session.flush()
        assert er.seat_holder_party(db_session, race) == "REP"


AFTER_CLOSE = datetime(2026, 11, 4, 3, 0)  # 10 PM ET, every covered state closed


def _sync(db, count, now=AFTER_CLOSE):
    async def fake(client, state, day):
        if isinstance(count, Exception):
            raise count
        return count

    with patch.object(er, "fetch_state_count", fake), patch.object(er, "utcnow", return_value=now), \
            patch.object(er, "send_ops_alert") as alert:
        out = asyncio.run(er.sync_state(db, None, "GA", DAY))
    return out, alert


class TestSyncState:
    def test_two_contests_for_one_seat_are_not_guessed_between(self, db_session):
        _setup(db_session)
        out, _ = _sync(db_session, _state(contests=[_contest(1, 2, 1), _contest(3, 4, 1)]))
        assert out["races"] == 0
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2") is None

    def test_a_contest_with_no_race_on_file_is_ignored(self, db_session):
        out, _ = _sync(db_session, _state(contests=[_contest(1, 2, 1, district=9)]))
        assert out == {"status": "ok", "races": 0, "contests": 1, "events": 0, "results": []}

    def test_unreadable_source_is_reported_not_raised(self, db_session):
        assert _sync(db_session, None)[0] == {"status": "unavailable"}


class TestTrustGates:
    def test_nothing_is_read_before_the_states_polls_close(self, db_session):
        _setup(db_session)
        out, _ = _sync(db_session, _state(contests=[_contest(1, 2, 1)]), now=datetime(2026, 11, 3, 23, 30))
        assert out == {"status": "polls_open", "pollsClose": "2026-11-04T00:00:00Z"}  # 7 PM ET
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2") is None

    def test_test_data_is_refused_and_alerted(self, db_session):
        from app.pipeline.fetch.election_results import UntrustedCount

        _setup(db_session)
        out, alert = _sync(db_session, UntrustedCount("GA is not production data"))
        assert out["status"] == "untrusted"
        # One refusal is not a page: a feed republished mid-read is refused
        # once and reads cleanly on the next pass.
        assert not alert.called
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2") is None
        _, alert = _sync(db_session, UntrustedCount("GA is not production data"))
        assert alert.called

    def test_a_transient_refusal_does_not_spend_a_later_ones_alert(self, db_session):
        from app.pipeline.fetch.election_results import UntrustedCount

        _setup(db_session)
        _sync(db_session, UntrustedCount("GA changed version mid-read (v7 then v8)"))
        _sync(db_session, UntrustedCount("GA changed version mid-read (v8 then v9)"))
        first = er.refusal_kind("GA changed version mid-read (v7 then v8)")
        assert first == er.refusal_kind("GA changed version mid-read (v8 then v9)")
        # A different refusal after the transient one is its first
        # occurrence: no alert yet.
        _, alert = _sync(db_session, UntrustedCount("GA results site is in demo mode"))
        assert not alert.called
        _, alert = _sync(db_session, UntrustedCount("GA results site is in demo mode"))
        assert alert.called
        assert alert.call_args.kwargs["dedupe_key"].endswith(er.refusal_kind("GA results site is in demo mode"))
        assert er.refusal_kind("GA results site is in demo mode") != first

    def test_a_feed_that_goes_backwards_is_not_stored(self, db_session):
        _setup(db_session)
        newer = _state(contests=[_contest(100, 90, 20)], source_updated=datetime(2026, 11, 4, 2, 0))
        older = _state(contests=[_contest(10, 9, 2)], source_updated=datetime(2026, 11, 4, 1, 0))
        assert _sync(db_session, newer)[0]["status"] == "ok"
        out, _ = _sync(db_session, older)
        assert out["status"] == "stale"
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2").votes_counted == 190

    def test_a_version_that_goes_backwards_is_not_stored(self, db_session):
        _setup(db_session)
        stamp = datetime(2026, 11, 4, 2, 0)
        assert _sync(db_session, _state(contests=[_contest(100, 90, 20)], source_updated=stamp, source_version="316199"))[0]["status"] == "ok"
        out, _ = _sync(db_session, _state(contests=[_contest(1, 1, 1)], source_updated=stamp, source_version="316100"))
        assert out["status"] == "stale"

    def test_a_count_from_the_future_is_not_stored(self, db_session):
        _setup(db_session)
        out, _ = _sync(db_session, _state(contests=[_contest(1, 2, 1)], source_updated=datetime(2026, 11, 5)))
        assert out["status"] == "stale"

    def test_an_impossible_count_is_dropped(self, db_session):
        _setup(db_session)
        out, _ = _sync(db_session, _state(contests=[_contest(100, 90, 120, total=100)]))
        assert out["races"] == 0

    def test_a_falling_total_is_stored_but_announces_nothing(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 800, 70))
        kinds, result = _apply(db_session, race, _contest(700, 790, 70))  # R now leads, but totals fell
        assert kinds == [] and result.votes_counted == 1490
        assert _issues(db_session) == []
        # The next poll, totals rising again, announces what the count holds.
        kinds, _ = _apply(db_session, race, _contest(750, 850, 75))
        assert er.FLIP in kinds

    def test_official_does_not_bypass_the_reporting_floor(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(90, 100, 10), official=True)
        assert _issues(db_session) == []


class TestStalledFeeds:
    def test_alerts_on_a_count_that_stopped_moving_with_units_out(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(100, 90, 50))
        result = db_session.get(RaceResult, race.id)
        result.last_change_at = datetime(2026, 11, 4, 1, 0)
        with patch.object(er, "utcnow", return_value=datetime(2026, 11, 4, 4, 0)), \
                patch.object(er, "live_results_states", return_value={"GA"}), \
                patch.object(er, "send_ops_alert") as alert:
            assert er.check_stalled_feeds(db_session, DAY) == ["GA"]
        assert alert.called

    def test_a_complete_count_is_not_stalled(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(100, 90, 100))
        db_session.get(RaceResult, race.id).last_change_at = datetime(2026, 11, 4, 1, 0)
        with patch.object(er, "utcnow", return_value=datetime(2026, 11, 4, 9, 0)), \
                patch.object(er, "live_results_states", return_value={"GA"}), \
                patch.object(er, "send_ops_alert"):
            assert er.check_stalled_feeds(db_session, DAY) == []


def test_a_matched_candidate_shows_their_ballot_name(db_session):
    race = _setup(db_session)
    db_session.get(Candidate, "H6GA02002").ballot_name = "Ray Jones"
    contest = ContestCount(office="H", district=2, candidates=[("Congressman Ray Jones", "R", 10), ("Dana Smith", "D", 5)])
    tallies = json.loads(er.apply_count(db_session, race, contest, _state(), DAY).result.tallies)
    assert tallies[0]["name"] == "Ray Jones" and tallies[0]["candidateId"] == "H6GA02002"


def test_an_unmatched_candidates_party_is_the_same_vocabulary_as_the_holders(db_session):
    """A feed name that matches none of the race's candidates keeps the
    feed's party letter; it must still compare equal to the holder's
    partyGroup, or a Republican leading a Republican seat reads as a flip."""
    race = _setup(db_session, held_by="R")
    contest = ContestCount(office="H", district=2, candidates=[("Unknown Person", "R", 900), ("Other Person", "D", 100)],
                           reporting_units=90, total_units=100)
    applied = er.apply_count(db_session, race, contest, _state(), DAY)
    db_session.flush()
    assert json.loads(applied.result.tallies)[0]["party"] == "REP"
    assert er.is_flip(applied.result) is False
    signals.update_developing_issues(db_session, [applied])
    assert _issues(db_session) == []


class TestOneStoryPerPoll:
    def test_a_flip_is_not_also_a_lead_change(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(100, 90, 10))
        kinds, _ = _apply(db_session, race, _contest(900, 1000, 60))
        assert kinds == [er.FLIP]

    def test_complete_first_returns_are_just_all_in(self, db_session):
        race = _setup(db_session)
        kinds, _ = _apply(db_session, race, _contest(100, 90, 100))
        assert kinds == [er.ALL_REPORTING]


class TestAnnouncedBaseline:
    def test_what_first_appears_in_a_held_poll_is_announced_next_poll(self, db_session):
        """A correction that lowers the total in the same publish that marks
        the count official: stored, silent, then announced."""
        race = _setup(db_session)
        _apply(db_session, race, _contest(200, 150, 100))
        kinds, _ = _apply(db_session, race, _contest(199, 150, 100), official=True)
        assert kinds == []
        kinds, _ = _apply(db_session, race, _contest(199, 150, 100), official=True)
        assert kinds == [er.OFFICIAL]

    def test_a_lead_change_inside_a_held_poll_is_announced_next_poll(self, db_session):
        race = _setup(db_session)
        db_session.query(Representative).delete()  # no known holder: a lead change, never a flip
        _apply(db_session, race, _contest(200, 150, 100))
        _apply(db_session, race, _contest(140, 160, 100))  # held: total fell
        kinds, _ = _apply(db_session, race, _contest(141, 160, 100))
        assert kinds == [er.LEAD_CHANGE]


class TestIssueLifecycle:
    def test_an_issue_the_action_center_retired_stays_retired_while_the_flip_merely_holds(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        issue.is_current = False  # the hourly refresh's 24h retirement
        _apply(db_session, race, _contest(1000, 1100, 70))
        assert issue.is_current is False

    def test_the_issue_is_dated_the_day_it_last_spoke(self, db_session):
        race = _setup(db_session)
        with patch("app.election_phase.election_today", return_value=date(2026, 11, 3)):
            _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        assert issue.date == "2026-11-03"
        with patch("app.election_phase.election_today", return_value=date(2026, 11, 4)):
            _apply(db_session, race, _contest(1000, 1100, 70))
        assert issue.date == "2026-11-04"

    def test_a_retired_issue_keeps_its_figures_current_without_moving_up(self, db_session):
        race = _setup(db_session)
        with patch("app.election_phase.election_today", return_value=date(2026, 11, 3)):
            _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        issue.is_current = False
        with patch("app.election_phase.election_today", return_value=date(2026, 11, 5)):
            _apply(db_session, race, _contest(1000, 1200, 90))
        assert issue.date == "2026-11-03"
        assert any("1,200 votes" in f for f in json.loads(issue.facts))

    def test_the_holder_fact_is_credited_to_civitas_not_the_state(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        facts, sources = json.loads(issue.facts), json.loads(issue.fact_sources)
        assert len(facts) == len(sources)
        assert sources[-1] == signals.HOLDER_SOURCE and facts[-1].startswith("The seat is held by")
        assert set(sources[:-1]) == {"Georgia Secretary of State"}

    def test_a_held_poll_leaves_the_issues_time_and_official_flag_with_its_figures(self, db_session):
        """A poll whose total fell is stored on the count row but never
        rewrites the issue; the issue's time and flag must not move either."""
        race = _setup(db_session)
        first = datetime(2026, 11, 4, 2)
        with patch.object(er, "utcnow", return_value=first):
            _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        assert issue.count_as_of == first and issue.count_official is False
        with patch.object(er, "utcnow", return_value=first + timedelta(hours=1)):
            _apply(db_session, race, _contest(800, 900, 60), official=True)  # total fell: held
        assert db_session.get(RaceResult, race.id).official is True
        assert issue.count_as_of == first and issue.count_official is False

    def test_an_official_revert_does_not_say_not_final(self, db_session):
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        _apply(db_session, race, _contest(1300, 1100, 100), official=True)
        assert "The state lists this count as official." in issue.summary
        assert "not final" not in issue.summary

    def test_a_reverted_flip_says_so_where_it_is_still_shown(self, db_session):
        """The homepage record and the issue's own address show retired
        rows; retiring alone left "leads in a seat Democrats hold" there."""
        race = _setup(db_session)
        _apply(db_session, race, _contest(900, 1000, 60))
        [issue] = _issues(db_session)
        _apply(db_session, race, _contest(1300, 1100, 80))
        assert issue.is_current is False
        assert issue.title == "Georgia's 2nd Congressional District count no longer shows a change of party"
        assert "latest count shows Dana Smith (D) ahead" in issue.summary
        assert "leads" not in issue.title
        # Tied: nobody named as ahead.
        _apply(db_session, race, _contest(1300, 1300, 85))
        assert "top two tied" in issue.summary
        # Flipped again: a new flip story; this one keeps the reversal.
        _apply(db_session, race, _contest(1300, 1500, 90))
        assert issue.is_current is False and "no longer shows" in issue.title
        assert any(i.is_current and "leads" in i.title for i in _issues(db_session))


class TestWholePass:
    def test_states_are_read_at_once_and_each_read_is_recorded(self, db_session):
        """A slow or broken feed no longer holds up the states after it, and
        the page can tell "couldn't read" from "nothing counted yet"."""
        from app.models import LiveResultRead

        _setup(db_session)
        log = []

        async def fake(client, state, day):
            log.append(("start", state))
            await asyncio.sleep(0.01)
            log.append(("end", state))
            if state == "CO":
                raise RuntimeError("feed down")
            return _state(contests=[_contest(100, 90, 60)])

        with patch.object(er, "live_results_states", return_value={"CO", "GA"}), \
                patch.object(er, "fetch_state_count", fake), \
                patch.object(er, "utcnow", return_value=AFTER_CLOSE), \
                patch.object(er, "send_ops_alert"):
            summary = asyncio.run(er.sync_live_results(db_session, None, DAY))

        assert [kind for kind, _ in log[:2]] == ["start", "start"]  # both in flight at once
        assert summary["CO"]["status"] == "failed" and summary["GA"]["status"] == "ok"
        co = db_session.get(LiveResultRead, ("CO", DAY.isoformat()))
        ga = db_session.get(LiveResultRead, ("GA", DAY.isoformat()))
        assert (co.status, co.last_ok_at) == ("failed", None)
        assert (ga.status, ga.last_ok_at) == ("ok", AFTER_CLOSE)
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2").votes_counted == 190


class TestCountyUnits:
    """Where units are counties, a county "reports" on its first batch."""

    def _race(self, db):
        race = Race(id="2026-HOUSE-CO-8", cycle_year=2026, office="H", state="CO", district=8)
        db.add(race)
        db.add(Representative(id="C000008", name="Holder", state="CO", district=8, party="D"))
        db.flush()
        return race

    def _apply_at(self, db, race, contest, at):
        with patch.object(er, "utcnow", return_value=at):
            return _apply(db, race, contest, unit_label="counties")

    def test_every_county_in_is_not_enough_on_the_first_dumps(self, db_session):
        race = self._race(db_session)
        first = datetime(2026, 11, 4, 2)
        self._apply_at(db_session, race, _contest(0, 0, 0, total=3, district=8), first - timedelta(hours=1))
        kinds, result = self._apply_at(db_session, race, _contest(90, 100, 3, total=3, district=8), first)
        assert er.FLIP not in kinds
        assert result.first_reported_at == first  # the first votes, not the zero-vote read
        assert not er.flip_qualifies(result, now=first + timedelta(hours=5))
        assert er.flip_qualifies(result, now=first + er.COUNTY_FLIP_SETTLE)

    def test_the_flip_is_announced_once_the_count_has_settled(self, db_session):
        race = self._race(db_session)
        first = datetime(2026, 11, 4, 2)
        self._apply_at(db_session, race, _contest(90, 100, 3, total=3, district=8), first)
        kinds, _ = self._apply_at(db_session, race, _contest(95, 110, 3, total=3, district=8),
                                  first + er.COUNTY_FLIP_SETTLE)
        assert er.FLIP in kinds

    def test_half_the_counties_never_qualifies(self, db_session):
        race = self._race(db_session)
        first = datetime(2026, 11, 4, 2)
        _, result = self._apply_at(db_session, race, _contest(90, 100, 2, total=3, district=8), first)
        assert not er.flip_qualifies(result, now=first + timedelta(days=3))


class TestRoundTwo:
    def test_a_candidate_row_appearing_is_not_a_lead_change(self, db_session):
        """The ballot sync runs through the count; matching the same leader
        to a new Candidate row re-keyed them ("X moves ahead of X")."""
        race = Race(id="2026-HOUSE-GA-2", cycle_year=2026, office="H", state="GA", district=2)
        db_session.add(race)
        db_session.add(Representative(id="S000001", name="Dana Smith", state="GA", district=2, party="D"))
        db_session.flush()
        _apply(db_session, race, _contest(100, 90, 5))
        before = db_session.get(RaceResult, race.id).last_change_at
        db_session.add(Candidate(id="H6GA02001", race_id=race.id, name="SMITH, DANA", party="DEM"))
        db_session.flush()
        with patch.object(er, "utcnow", return_value=utcnow() + timedelta(minutes=5)):
            kinds, result = _apply(db_session, race, _contest(100, 90, 5))
        assert kinds == []
        assert result.last_change_at == before

    def test_a_redrawn_states_house_seat_has_no_holder(self, db_session):
        race = Race(id="2026-HOUSE-UT-3", cycle_year=2026, office="H", state="UT", district=3)
        db_session.add(race)
        db_session.add(Representative(id="U3", name="Holder", state="UT", district=3, party="R"))
        db_session.flush()
        assert "UT" in er.redrawn_states(2026)
        # Listed, but on its old map (its redraw was stayed): its seats keep a holder.
        assert "MO" not in er.redrawn_states(2026)
        assert er.seat_holder_party(db_session, race) is None

    def test_first_returns_that_are_already_a_flip_are_one_story(self, db_session):
        race = _setup(db_session)
        kinds, _ = _apply(db_session, race, _contest(10, 90, 60))
        assert kinds == [er.FLIP]

    def test_a_version_only_source_is_protected_from_rollback(self, db_session):
        race = _setup(db_session)
        er.apply_count(db_session, race, _contest(10, 9, 5), _state(source_version="12"), DAY)
        db_session.flush()
        problem = er.freshness_problem(db_session, "GA", DAY, _state(source_version="11"))
        assert problem == "source version went back from 12 to 11"


class TestRoundThree:
    def test_a_house_special_on_the_same_ballot_leaves_the_regular_count(self, db_session):
        _setup(db_session)
        special = _contest(5, 6, 1)
        special.is_special = True
        out, _ = _sync(db_session, _state(contests=[_contest(100, 90, 60), special]))
        assert out["races"] == 1
        assert db_session.get(RaceResult, "2026-HOUSE-GA-2").votes_counted == 190

    def test_a_senate_special_with_no_special_race_is_not_the_regular_race(self, db_session):
        db_session.add(Race(id="2026-SEN-GA", cycle_year=2026, office="S", state="GA"))
        db_session.flush()
        special = _contest(5, 6, 1, office="S", district=None)
        special.is_special = True
        assert er._contest_race(db_session, 2026, "GA", special) is None
        regular = _contest(5, 6, 1, office="S", district=None)
        assert er._contest_race(db_session, 2026, "GA", regular).id == "2026-SEN-GA"

    def test_a_ballot_name_matched_mid_count_is_not_a_lead_change(self, db_session):
        """The display name becomes the Candidate's ballot_name once matched;
        comparing by it read "Ray Jones moves ahead of Congressman Ray
        Jones"."""
        race = Race(id="2026-HOUSE-GA-2", cycle_year=2026, office="H", state="GA", district=2)
        db_session.add(race)
        db_session.flush()
        contest = ContestCount(office="H", district=2, reporting_units=5, total_units=100,
                               candidates=[("Congressman Ray Jones", "R", 100), ("Dana Smith", "D", 90)])
        _apply(db_session, race, contest)
        db_session.add(Candidate(id="H6GA02002", race_id=race.id, name="JONES, RAY", party="REP", ballot_name="Ray Jones"))
        db_session.flush()
        db_session.expire(race)  # its candidates, as the next pass loads them
        with patch.object(er, "utcnow", return_value=utcnow() + timedelta(minutes=5)):
            kinds, result = _apply(db_session, race, contest)
        assert kinds == []
        assert json.loads(result.tallies)[0]["name"] == "Ray Jones"


def test_a_holder_unknown_when_the_count_began_is_read_again(db_session):
    """A data reset wipes the members a seat's holder is read from; a count
    rebuilt before they were kept no holder all night — no flip announced."""
    race = _setup(db_session)
    db_session.query(Representative).delete()
    db_session.flush()
    kinds, result = _apply(db_session, race, _contest(400, 600, 60))
    assert result.held_by_party is None and "flip" not in kinds
    db_session.add(Representative(id="S000001", name="Dana Smith", state="GA", district=2, party="D"))
    db_session.flush()
    kinds, result = _apply(db_session, race, _contest(410, 620, 62))
    assert result.held_by_party is not None
    assert "flip" in kinds


@pytest.fixture
def _on_election_day():
    """Issues are dated election_today(); relinking only takes this
    election's."""
    with patch("app.election_phase.election_today", return_value=DAY):
        yield


ELECTION_NIGHT = datetime(2026, 11, 4, 2, 0)  # 9pm Eastern, naive UTC


def _reset(db, members=False):
    """A data reset: the count and its events go, the issues stay (made on
    election night — the tests run on another date). With members=True the
    members a seat's holder is read from go too."""
    for issue in _issues(db):
        issue.created_at = ELECTION_NIGHT
    if members:
        db.query(Representative).delete()
    db.query(ElectionResultEvent).delete()
    db.query(RaceResult).delete()
    db.flush()


def test_a_reset_relinks_the_races_developing_issue(db_session, _on_election_day):
    """The rebuilt row started unlinked, so a reverted count left the issue
    current, and a later flip opened a second one."""
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    _reset(db_session)
    _apply(db_session, race, _contest(700, 500, 70))  # rebuilt: the holder leads
    db_session.refresh(issue)
    assert not issue.is_current and "no longer shows a change of party" in issue.title
    assert db_session.get(RaceResult, race.id).developing_issue_id == issue.id
    _apply(db_session, race, _contest(700, 900, 80))  # a new flip: a new story
    assert len(_issues(db_session)) == 2
    assert sum(i.is_current for i in _issues(db_session)) == 1


def test_a_reset_keeps_a_promoted_story_with_the_news(db_session, _on_election_day):
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    issue.status = ActionIssueStatus.CONFIRMED
    _reset(db_session)
    _apply(db_session, race, _contest(400, 650, 70))
    assert _issues(db_session) == [issue]


def test_a_reset_does_not_revive_an_issue_retired_while_the_flip_held(db_session, _on_election_day):
    """The Action Center retires an unmatched developing issue a day on;
    the rebuilt count's first read raising its flip afresh is not news."""
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    issue.is_current = False
    _reset(db_session)
    _apply(db_session, race, _contest(400, 650, 70))
    assert _issues(db_session) == [issue] and not issue.is_current


def test_a_reset_after_a_reversal_then_a_flip_is_a_new_story(db_session, _on_election_day):
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    _apply(db_session, race, _contest(700, 600, 65))  # reverted: the issue says so and retires
    _reset(db_session)
    _apply(db_session, race, _contest(700, 900, 80))
    assert len(_issues(db_session)) == 2


def test_the_race_link_never_matches_a_longer_id(db_session, _on_election_day):
    """GA-1 must not read GA-12's issue: json.dumps closes the id with a
    quote."""
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    issue.actions = issue.actions.replace("#race-2026-HOUSE-GA-2", "#race-2026-HOUSE-GA-12")
    db_session.flush()
    by_race = signals._issues_by_race(db_session)
    assert set(by_race) == {"2026-HOUSE-GA-12"}
    assert "2026-HOUSE-GA-1" not in by_race


def test_a_reset_that_took_the_holder_leaves_the_issue_alone(db_session, _on_election_day):
    """With no holder the count can't say whether the seat still changes
    party; retiring then said "no longer shows a change of party" while the
    challenger led."""
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    _reset(db_session, members=True)
    _apply(db_session, race, _contest(400, 650, 70))
    assert issue.is_current and "no longer" not in issue.title
    db_session.add(Representative(id="S000001", name="Dana Smith", state="GA", district=2, party="D"))
    db_session.flush()
    _apply(db_session, race, _contest(400, 700, 75))  # the holder is back: still the same story
    assert _issues(db_session) == [issue] and issue.is_current


def test_a_zero_read_does_not_retire_a_flip(db_session, _on_election_day):
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    _reset(db_session)
    _apply(db_session, race, _contest(0, 0, 0))
    assert issue.is_current


def test_a_runoff_does_not_take_the_generals_promoted_story(db_session, _on_election_day):
    """A promoted story's date moves with each news match; its created_at
    doesn't."""
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    issue.status = ActionIssueStatus.CONFIRMED
    issue.date = "2026-12-05"
    _reset(db_session)
    result = RaceResult(race_id=race.id, election_date="2026-12-01", source_name="x", tallies="[]",
                        held_by_party="D", votes_counted=10)
    db_session.add(result)
    db_session.flush()
    signals.update_developing_issues(db_session, [er.Applied(result, created=True)])
    assert result.developing_issue_id is None


def test_a_reset_that_took_the_holder_does_not_revive_a_retired_flip(db_session, _on_election_day):
    """The holder comes back a poll after the rebuild, and the sync raises
    the flip afresh then — the relink was a poll earlier."""
    race = _setup(db_session)
    _apply(db_session, race, _contest(400, 600, 60))
    [issue] = _issues(db_session)
    issue.is_current = False  # the Action Center's one-day rule, the flip still holding
    _reset(db_session, members=True)
    _apply(db_session, race, _contest(400, 650, 70))
    db_session.add(Representative(id="S000001", name="Dana Smith", state="GA", district=2, party="D"))
    db_session.flush()
    kinds, _ = _apply(db_session, race, _contest(400, 700, 75))
    assert "flip" in kinds
    assert _issues(db_session) == [issue] and not issue.is_current
