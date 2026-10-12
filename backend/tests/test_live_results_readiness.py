"""Before election night (live_results/readiness.py), and the fixed facts
the night is gated on: when each covered state's polls close, and which
states are never read live."""

from datetime import date, datetime

import pytest

from app.live_results import readiness
from app.models import Race
from app.pipeline.fetch import election_results, poll_close
from app.pipeline.fetch.election_results import ContestCount, StateCount, UntrustedCount

DAY = date(2026, 11, 3)


def _count(*contests):
    return StateCount(source_name="X", page_url=None, official=False, contests=list(contests))


@pytest.fixture()
def alerts(monkeypatch):
    sent, resolved = [], []
    monkeypatch.setattr(readiness, "send_ops_alert", lambda subject, body, **kw: sent.append((subject, body, kw)))
    monkeypatch.setattr(readiness, "resolve_ops_alert", lambda condition: resolved.append(condition))
    return sent, resolved


@pytest.fixture()
def reads(monkeypatch):
    """{state: StateCount | None | Exception} served as fetch_state_count."""
    served = {}

    async def fetch(client, state, election_day):
        got = served[state]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(readiness, "fetch_state_count", fetch)
    monkeypatch.setattr(readiness, "live_results_states", lambda: set(served))
    return served


class TestReadiness:
    def test_the_window_is_the_two_weeks_before_and_never_election_day(self):
        assert readiness.readiness_due(DAY, date(2026, 10, 20))
        assert readiness.readiness_due(DAY, date(2026, 11, 2))
        assert not readiness.readiness_due(DAY, date(2026, 10, 19))
        assert not readiness.readiness_due(DAY, DAY)
        assert not readiness.readiness_due(DAY, date(2026, 11, 4))

    async def test_each_kind_of_problem_is_alerted_and_a_ready_state_cleared(self, db_session, alerts, reads):
        for rid, st, office, district in (("2026-SEN-NE", "NE", "S", None), ("2026-HOUSE-NE-1", "NE", "H", 1),
                                          ("2026-HOUSE-ND-0", "ND", "H", 0), ("2026-HOUSE-VA-2", "VA", "H", 2)):
            db_session.add(Race(id=rid, cycle_year=2026, office=office, state=st, district=district))
        db_session.flush()
        reads.update({
            # Ready: a contest for every race.
            "ND": _count(ContestCount(office="H", district=None, candidates=[("A", "R", 0)])),
            # New Mexico's and Montana's hosts, three weeks out: redirected
            # to a landing page, so the reader finds no general.
            "NM": None,
            "MT": RuntimeError("boom"),
            "GA": UntrustedCount("GA Enhanced Voting election x is not production data"),
            # The Senate contest is missing (a label the reader can't parse).
            "NE": _count(ContestCount(office="H", district=1, candidates=[("A", "R", 0)])),
            # Two contests read, neither mapped to the race on file.
            "VA": _count(ContestCount(office="H", district=3, candidates=[("A", "D", 0)])),
        })
        problems = await readiness.check_feed_readiness(db_session, None, DAY, date(2026, 10, 25))
        sent, resolved = alerts
        assert set(problems) == {"NM", "MT", "GA", "NE", "VA"}
        assert "not staged yet, moved, or unreadable" in problems["NM"]
        assert "RuntimeError" in problems["MT"]
        assert "not production data" in problems["GA"]
        assert problems["NE"].endswith("none for 2026-SEN-NE (a label the reader can't parse, or a contest not "
                                       "staged yet)")
        assert "2026-HOUSE-VA-2" in problems["VA"]
        assert resolved == ["results-readiness-ND"]
        by_state = {s.split(": ")[1]: kw for s, _, kw in sent}
        assert by_state["NM"] == {"dedupe_key": "results-readiness-NM-2026-11-03-2026-10-25",
                                  "condition": "results-readiness-NM"}


class TestPollClose:
    """Every covered state's last close on 2026-11-03, in UTC. Daylight
    saving time ended on 2026-11-01, so every zone is on standard time:
    Eastern UTC-5 ... Hawaii-Aleutian UTC-10."""

    EXPECTED = {
        "GA": (0, 0), "SC": (0, 0), "VA": (0, 0),  # 7 PM Eastern
        "WV": (0, 30),  # 7:30 PM Eastern
        "RI": (1, 0),  # 8 PM Eastern
        "AR": (1, 30),  # 7:30 PM Central
        "IA": (2, 0), "NE": (2, 0),  # 8 PM Central (Nebraska's west: 7 PM Mountain)
        "CO": (2, 0), "NM": (2, 0),  # 7 PM Mountain
        "MT": (3, 0), "UT": (3, 0),  # 8 PM Mountain
        "ND": (4, 0),  # latest statutory close, 9 PM Mountain
        "ID": (4, 0), "WA": (4, 0),  # 8 PM Pacific (northern Idaho)
    }

    def test_every_live_state_closes_when_its_statute_says(self):
        assert set(self.EXPECTED) == election_results.live_results_states()
        for state, (hour, minute) in self.EXPECTED.items():
            assert poll_close.last_poll_close(state, DAY) == datetime(2026, 11, 4, hour, minute), state

    def test_a_close_one_second_early_is_still_open(self):
        assert not poll_close.polls_closed("GA", DAY, datetime(2026, 11, 3, 23, 59, 59))
        assert poll_close.polls_closed("GA", DAY, datetime(2026, 11, 4, 0, 0))

    def test_a_general_still_on_daylight_time_closes_an_hour_earlier_in_utc(self):
        # 2032-11-02: the first Monday of November is the 1st, so election
        # day comes before daylight time ends (Nov 7). 7 PM MDT is 01:00Z.
        assert poll_close.last_poll_close("CO", date(2032, 11, 2)) == datetime(2032, 11, 3, 1, 0)
        assert poll_close.last_poll_close("WA", date(2032, 11, 2)) == datetime(2032, 11, 3, 3, 0)


class TestRankedChoice:
    def test_maine_and_alaska_are_ranked_choice_states(self):
        assert election_results.ranked_choice_states() == {"AK", "ME"}

    def test_a_ranked_choice_state_is_never_read_live(self, monkeypatch):
        real = election_results.source_for_state

        def source(state):
            if state == "ME":  # as if Maine's entry named a vendor this reads
                return {**real("ME"), "strategy": "clarity"}
            return real(state)

        monkeypatch.setattr(election_results, "source_for_state", source)
        assert election_results._reader_for(source("ME")) is not None
        assert "ME" not in election_results.live_results_states()
