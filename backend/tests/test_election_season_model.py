"""election_phase.season: the six stretches /elections and its hub are laid
out by, at every boundary between them. Dates are derived from the statutory
rule (next_election_day) and the FEC calendar rows each test stores, never
assumed."""

from datetime import date, datetime, timedelta

import pytest

from app import election_phase as ep
from app.election_calendar import new_congress_day, next_election_day
from app.models import FederalElectionDate, Race, RaceResult

ELECTION = next_election_day(date(2026, 1, 1))
NEW_CONGRESS = new_congress_day(ELECTION)
NEXT = next_election_day(ELECTION)


def _date(db, day: str, code: str, office: str = "H", special: bool = False, state: str = "TX"):
    db.add(FederalElectionDate(
        id=f"{day}-{state}-{office}-0-{code}", election_date=day, election_year=int(day[:4]), state=state,
        office=office, type_code=code, is_special=special, listed_on="2027-01-01", official=False,
    ))
    db.commit()


def _name(db, day: date) -> str:
    return ep.season(db, day).name


class TestAroundElectionDay:
    def test_campaign_up_to_the_day(self, db_session):
        assert _name(db_session, ELECTION - timedelta(days=1)) == ep.SEASON_CAMPAIGN

    def test_election_night(self, db_session):
        s = ep.season(db_session, ELECTION)
        assert s.name == ep.SEASON_ELECTION_NIGHT and s.cycle == ELECTION.year and s.until == ELECTION

    def test_results_window_with_no_count_is_the_grace_period(self, db_session):
        last = ELECTION + timedelta(days=ep.RESULTS_GRACE_DAYS)
        assert _name(db_session, ELECTION + timedelta(days=1)) == ep.SEASON_RESULTS
        assert ep.season(db_session, last).until == last
        assert _name(db_session, last) == ep.SEASON_RESULTS
        after = ep.season(db_session, last + timedelta(days=1))
        assert after.name == ep.SEASON_POST_CERTIFICATION
        # The site's election has moved on; the archive's is the one held.
        assert after.cycle == NEXT.year and after.held == ELECTION.year
        assert after.until == NEW_CONGRESS - timedelta(days=1) and after.between_cycles

    def test_a_count_still_moving_holds_results_to_january_3(self, db_session):
        db_session.add(Race(id="r", state="GA", office="S", cycle_year=ELECTION.year))
        db_session.add(RaceResult(race_id="r", election_date=ELECTION.isoformat(), source_name="GA SoS",
                                  last_change_at=datetime(NEW_CONGRESS.year - 1, 12, 30, 15)))
        db_session.commit()
        assert _name(db_session, NEW_CONGRESS) == ep.SEASON_RESULTS
        # Seated: no post-certification stretch is left.
        assert _name(db_session, NEW_CONGRESS + timedelta(days=1)) == ep.SEASON_OFF


class TestBetweenCycles:
    def test_january_3_opens_the_off_season(self, db_session):
        assert _name(db_session, NEW_CONGRESS - timedelta(days=1)) == ep.SEASON_POST_CERTIFICATION
        s = ep.season(db_session, NEW_CONGRESS)
        assert s.name == ep.SEASON_OFF and s.until is None and s.first_primary is None

    @pytest.mark.parametrize("month, day", [(3, 1), (7, 15), (11, 2), (12, 31)])
    def test_the_odd_year_is_off_season_with_no_calendar(self, db_session, month, day):
        assert _name(db_session, date(NEW_CONGRESS.year, month, day)) == ep.SEASON_OFF

    def test_without_a_calendar_the_election_year_is_campaign(self, db_session):
        assert _name(db_session, date(NEXT.year, 2, 1)) == ep.SEASON_CAMPAIGN


class TestPrimarySeason:
    @pytest.fixture
    def calendar(self, db_session):
        caucus = date(NEXT.year, 1, 15)
        _date(db_session, caucus.isoformat(), "CAU", office="P", state="IA")
        _date(db_session, f"{NEXT.year}-03-07", "P")
        _date(db_session, f"{NEXT.year}-06-30", "P", office="P", state="NJ")
        _date(db_session, f"{NEXT.year}-09-12", "P", state="NH")
        _date(db_session, f"{NEXT.year}-09-26", "PR", office="S", state="GA")
        # Neither do a primary on or after election day (an open primary
        # and its runoff) nor a territory's delegate primary.
        _date(db_session, NEXT.isoformat(), "P", state="LA")
        _date(db_session, (NEXT + timedelta(days=39)).isoformat(), "PR", state="LA")
        _date(db_session, f"{NEXT.year}-10-01", "P", state="GU")
        # Specials never move the season.
        _date(db_session, f"{NEXT.year}-10-20", "SP", special=True, state="FL")
        _date(db_session, f"{NEW_CONGRESS.year}-04-01", "SG", special=True, state="FL")
        return caucus

    def test_off_season_until_the_first_caucus(self, db_session, calendar):
        s = ep.season(db_session, calendar - timedelta(days=1))
        assert s.name == ep.SEASON_OFF and s.until == calendar - timedelta(days=1)
        assert s.first_primary == calendar

    def test_the_presidential_primaries_open_the_ramp(self, db_session, calendar):
        s = ep.season(db_session, calendar)
        assert s.name == ep.SEASON_RAMP and not s.between_cycles
        assert _name(db_session, date(NEXT.year, 2, 1)) == ep.SEASON_RAMP

    def test_the_ramp_ends_with_the_last_congressional_primary_or_runoff(self, db_session, calendar):
        last = date(NEXT.year, 9, 26)
        assert ep.season(db_session, last).until == last
        assert _name(db_session, last) == ep.SEASON_RAMP
        assert _name(db_session, last + timedelta(days=1)) == ep.SEASON_CAMPAIGN


def test_every_season_leads_with_every_page():
    pages = set(ep.SEASON_LEAD[ep.SEASON_CAMPAIGN])
    assert len(ep.SEASON_LEAD) == 6
    assert all(set(lead) == pages and len(lead) == len(pages) for lead in ep.SEASON_LEAD.values())


def test_as_json(db_session):
    body = ep.season(db_session, NEW_CONGRESS).as_json()
    assert body["season"] == "off_season" and body["betweenCycles"] is True
    assert body["heldCycle"] == ELECTION.year and body["cycle"] == NEXT.year
    assert body["lead"][0] == "calendar" and body["electionDay"] == NEXT.isoformat()
