"""Tests for the /api/elections/* endpoints — calls router functions
directly with db_session (same convention as test_action_api.py), since
no TestClient+dependency-override harness exists in this test suite yet.
"""

import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import Candidate, Race, RaceCoverageItem


@pytest.fixture(autouse=True)
def _campaign_clock(monkeypatch):
    """These tests build a 2026-cycle roster. Pinned inside the 2026
    campaign: on the real clock, once the results window closes (Nov 18,
    2026) the site's cycle is 2028 and they read an empty roster. A test
    that needs another date patches election_today itself."""
    from datetime import date

    monkeypatch.setattr("app.election_phase.election_today", lambda: date(2026, 10, 1))


# Every fixture here is a 2026 race; pin the clock inside that cycle so the
# tests don't all fail on election night (conftest.freeze_utcnow).
@pytest.fixture(autouse=True)
def _in_the_2026_cycle(freeze_utcnow):
    freeze_utcnow(datetime(2026, 9, 30, 12, 0))


@pytest.fixture(autouse=True)
def _the_119th_sits(monkeypatch):
    """Redrawn states are measured against the sitting Congress's lines
    (live_results.sync.redrawn_states). Before Jan 3, 2027 that is the
    119th, on the old map; pin it so these 2026 fixtures don't flip then."""
    from app.config import settings

    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 119)


def _body(response):
    return json.loads(response.body)


def _race(db, race_id, state, office="S", district=None, cycle_year=2026):
    r = Race(id=race_id, cycle_year=cycle_year, office=office, state=state, district=district)
    db.add(r)
    return r


@pytest.fixture()
def on_date(monkeypatch, tmp_path):
    """Pin the clock the elections API reads the cycle from, and serve the
    bundled (checked-in) PVI file, so a test of which district lines a race
    shows means the same thing on any date it runs."""
    from app.pipeline.analyze import score_calculator
    from app.pipeline.fetch import district_pvi

    monkeypatch.setattr(score_calculator, "_PVI_PERSISTENT_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(score_calculator, "_district_pvi_cache", None)
    monkeypatch.setattr(score_calculator, "_district_pvi_stamp", None)
    monkeypatch.setattr(district_pvi, "_file_cache", None)
    monkeypatch.setattr(district_pvi, "_file_stamp", None)

    def set_clock(dt):
        # The cycle is the active election's (election_phase), read on the
        # Eastern calendar.
        from datetime import timezone

        from app.election_phase import ELECTION_TZ

        today = dt.replace(tzinfo=timezone.utc).astimezone(ELECTION_TZ).date()
        monkeypatch.setattr("app.election_phase.election_today", lambda: today)
        monkeypatch.setattr("app.pipeline.election_pipeline.utcnow", lambda: dt)

    return set_clock


def _candidate(db, cand_id, race_id, name, **overrides):
    defaults = dict(party="DEM")
    defaults.update(overrides)
    c = Candidate(id=cand_id, race_id=race_id, name=name, **defaults)
    db.add(c)
    return c


class TestListRaces:
    def test_returns_race_with_pvi_and_top_candidates(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", cash_on_hand=500.0)
        _candidate(db_session, "S2", "2026-SEN-GA", "COLLINS, JANE", cash_on_hand=200.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert len(data) == 1
        race = data[0]
        assert race["id"] == "2026-SEN-GA"
        assert race["candidateCount"] == 2
        assert race["topCandidates"][0]["id"] == "S1"  # higher cash_on_hand first
        assert isinstance(race["pvi"], int)  # GA has a real PVI entry
        assert race["pviLevel"] == "state"  # a Senate race's PVI is the statewide figure

    def test_stale_incumbent_flag_is_dropped_in_top_candidates_too(self, db_session):
        """Same correction as the ballot page and the candidate-detail
        page (see TestStaleIncumbentFlag / TestCandidateDetail) — this
        list backs the directory/map page, which also renders whatever
        incumbentChallenge topCandidates carries."""
        _race(db_session, "2026-SEN-MI", "MI")
        _candidate(db_session, "PETERS", "2026-SEN-MI", "PETERS, GARY", incumbent_challenge="I", cash_on_hand=500.0)
        _candidate(
            db_session, "ROGERS", "2026-SEN-MI", "ROGERS, MICHAEL J", party="REP",
            incumbent_challenge="O", cash_on_hand=200.0,
        )
        db_session.commit()

        data = _body(elections.list_races(db_session))
        top = {c["id"]: c for c in data[0]["topCandidates"]}
        assert top["PETERS"]["incumbentChallenge"] is None

    def test_house_race_uses_district_pvi_not_state_pvi(self, db_session, on_date):
        on_date(datetime(2026, 10, 1))
        _race(db_session, "2026-HOUSE-CA-12", "CA", office="H", district=12)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["pvi"] == elections._election_district_pvi(2026)[0]["CA-12"]
        # The provenance flag tells the frontend which map the number came
        # from — a district figure, not the statewide fallback.
        assert data[0]["pviLevel"] == "district"

    def test_2026_house_race_uses_the_lines_on_the_ballot(self, db_session, on_date):
        """TN redrew for 2026: the race is on the new TN-9 (R+9), while the
        sitting member — scored separately, from the bundled file's 119th-
        Congress table — was elected in a D+23 seat."""
        from app.pipeline.analyze import score_calculator

        on_date(datetime(2026, 10, 1))
        _race(db_session, "2026-HOUSE-TN-9", "TN", office="H", district=9)
        db_session.commit()
        data = _body(elections.list_races(db_session))
        assert data[0]["pvi"] == 9
        assert score_calculator.get_district_pvi_map()["TN-9"] == -23

    def test_pvi_map_serves_and_labels_the_election_lines(self, db_session, on_date):
        on_date(datetime(2026, 10, 1))
        body = _body(elections.pvi_map(db_session))
        assert body["cycleYear"] == 2026
        assert body["districts"]["TX-35"] == 4
        meta = body["meta"]["districts"]
        assert "2026" in meta["lines"]
        assert "revision 1374239063" in meta["source"]
        # Dated by the pinned revision, not by the day it was fetched.
        assert meta["asOf"] == "2026-09-10T19:01:33Z"
        assert meta["revision"]["revid"] == 1374239063
        assert (meta["congress"], meta["forCongress"]) == (120, 120)

    def test_after_election_day_the_next_cycle_keeps_the_new_lines(self, db_session, on_date):
        """Regression: once the 2026 results window closes (Nov 18 with no
        count moving — election_phase) the cycle is 2028 (121st Congress),
        which has no pinned table yet. The pages must stay on the lines
        just voted on — the 120th's — not fall back to the sitting 119th's,
        which put TX-35 back at D+19 on a map where it is R+4."""
        # Inside the results window the site is still on 2026 and its lines.
        on_date(datetime(2026, 11, 10, 15))
        body = _body(elections.pvi_map(db_session))
        assert body["cycleYear"] == 2026
        assert body["districts"]["TX-35"] == 4
        assert (body["meta"]["districts"]["congress"], body["meta"]["districts"]["forCongress"]) == (120, 120)

        on_date(datetime(2026, 11, 20, 15))
        body = _body(elections.pvi_map(db_session))
        assert body["cycleYear"] == 2028
        assert (body["districts"]["TX-35"], body["districts"]["TN-9"]) == (4, 9)
        meta = body["meta"]["districts"]
        assert (meta["congress"], meta["forCongress"]) == (120, 121)

        _race(db_session, "2028-HOUSE-TX-35", "TX", office="H", district=35, cycle_year=2028)
        db_session.commit()
        data = _body(elections.list_races(db_session))
        assert (data[0]["pvi"], data[0]["pviLevel"]) == (4, "district")
        detail = _body(elections.race_detail("2028-HOUSE-TX-35", db_session))
        assert detail["pvi"] == 4

    def test_only_current_cycle_races_returned(self, db_session):
        """Contract of /races: the CURRENT cycle only — load-bearing the
        day a second cycle's roster syncs into the same table."""
        _race(db_session, "2026-SEN-GA", "GA")
        _race(db_session, "2028-SEN-AZ", "AZ", cycle_year=2028)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert [r["id"] for r in data] == ["2026-SEN-GA"]

    def test_candidate_summary_exposes_status_and_sync_watermark(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(
            db_session, "S1", "2026-SEN-GA", "OSSOFF, JON",
            candidate_status="C", cash_on_hand=500.0,
            last_financials_sync=datetime(2026, 7, 20, 8, 30),
        )
        # Never-synced challenger: null watermark, not a fabricated time —
        # the frontend renders "awaiting FEC sync" instead of "$0 raised".
        _candidate(db_session, "S2", "2026-SEN-GA", "COLLINS, JANE")
        db_session.commit()

        data = _body(elections.list_races(db_session))
        synced, unsynced = data[0]["topCandidates"]
        assert synced["candidateStatus"] == "C"
        # Stored naive UTC must serialize with an explicit Z so JS Date
        # doesn't parse it as viewer-local time.
        assert synced["lastFinancialsSync"] == "2026-07-20T08:30:00Z"
        assert unsynced["lastFinancialsSync"] is None

    def test_confirmed_candidates_filter_out_defeated_primary_fec_filers(self, db_session):
        """Same filtering as the ballot page and race-detail route — a
        defeated-primary FEC filer shouldn't count toward candidateCount
        or edge out the real nominee for a topCandidates slot just
        because they raised more before losing."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "WINNER", "2026-SEN-GA", "PAXTON, KEN", confirmed_general=True, cash_on_hand=1.0)
        _candidate(db_session, "LOSER", "2026-SEN-GA", "CORNYN, JOHN", confirmed_general=False, cash_on_hand=999.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["candidateCount"] == 1
        assert [c["id"] for c in data[0]["topCandidates"]] == ["WINNER"]

    def test_primary_ballot_filters_when_no_nominee_is_confirmed_yet(self, db_session):
        """The months BEFORE a primary: nobody is a nominee yet, so the
        best available answer is who the state says is actually on its
        primary ballot — an FEC filer who never filed with the state is
        not a ballot option either."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "FILED", "2026-SEN-GA", "OSSOFF, JON",
                   on_primary_ballot=True, cash_on_hand=1.0)
        _candidate(db_session, "PAPER", "2026-SEN-GA", "NOBODY, A",
                   on_primary_ballot=False, cash_on_hand=999.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["candidateCount"] == 1
        assert [c["id"] for c in data[0]["topCandidates"]] == ["FILED"]

    def test_a_confirmed_nominee_outranks_the_primary_ballot(self, db_session):
        """Being on a primary ballot says nothing about surviving it, so
        once a state confirms nominees those win outright — including over
        someone who was on the primary ballot and lost."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "NOMINEE", "2026-SEN-GA", "PAXTON, KEN",
                   confirmed_general=True, on_primary_ballot=True, cash_on_hand=1.0)
        _candidate(db_session, "BEATEN", "2026-SEN-GA", "CORNYN, JOHN",
                   confirmed_general=False, on_primary_ballot=True, cash_on_hand=999.0)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert [c["id"] for c in data[0]["topCandidates"]] == ["NOMINEE"]


class TestRaceDetail:
    def test_404_for_unknown_race(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            elections.race_detail("nonexistent", db_session)
        assert exc_info.value.status_code == 404

    def test_returns_candidates_and_coverage(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON")
        db_session.add(RaceCoverageItem(
            race_id="2026-SEN-GA", source_type="news", source_name="AP News",
            title="Ossoff leads", url="https://apnews.com/a1", summary="Tight race.",
            published_at=datetime(2026, 7, 19, 14, 0),
        ))
        db_session.commit()

        data = _body(elections.race_detail("2026-SEN-GA", db_session))
        assert data["id"] == "2026-SEN-GA"
        assert data["pviLevel"] == "state"
        assert len(data["candidates"]) == 1
        assert len(data["coverage"]) == 1
        assert data["coverage"][0]["summary"] == "Tight race."  # verbatim
        # Explicit Z suffix — see _iso_utc (naive ISO parses as local time in JS).
        assert data["coverage"][0]["publishedAt"] == "2026-07-19T14:00:00Z"

    def test_confirmed_candidates_filter_out_defeated_primary_fec_filers(self, db_session):
        """Same real bug as test_elections_state_ballot's version of this
        test, but for the direct race-detail route: BallotRaceOptions
        links every ballot row to /elections/{race.id}, which calls this
        function — a candidate list unfiltered here would let the exact
        19-candidates bug resurface one click after the state-ballot page
        fixed it."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "WINNER", "2026-SEN-GA", "PAXTON, KEN", confirmed_general=True)
        _candidate(db_session, "LOSER", "2026-SEN-GA", "CORNYN, JOHN", confirmed_general=False)
        db_session.commit()

        data = _body(elections.race_detail("2026-SEN-GA", db_session))
        assert [c["id"] for c in data["candidates"]] == ["WINNER"]
        # _race_full computes this alongside the same filter; race_detail
        # must too, or a reader here can't tell a confirmed nominee from a
        # raw FEC filer the way the state-ballot page already says.
        assert data["candidateSource"] == "nominees"


class TestPviMap:
    def test_returns_both_state_and_district_maps(self, db_session):
        data = _body(elections.pvi_map(db_session))
        assert "AK" in data["states"]
        assert "AK-0" in data["districts"]

    def test_includes_provenance_metadata(self, db_session):
        """The bare numbers over-claim without provenance (2026-07 review
        F7) — the payload must carry per-map source metadata plus the
        lean-is-not-a-forecast note for the frontend to label."""
        data = _body(elections.pvi_map(db_session))
        meta = data["meta"]
        assert "states" in meta
        assert "districts" in meta
        assert "not" in meta["note"]  # the "measures lean, not who will win" caveat

    def test_includes_cycle_year(self, db_session):
        """Lets /elections's directory page get its header year from the
        same fetch it already makes for map coloring, instead of a
        second fetch of every race."""
        from app.pipeline.election_pipeline import current_election_cycle

        data = _body(elections.pvi_map(db_session))
        assert data["cycleYear"] == current_election_cycle(db_session)

    def test_includes_next_election_day(self, db_session, monkeypatch):
        """The /elections masthead's countdown. A date, not a day count:
        the response is cached, and a count would go stale with it."""
        from datetime import date

        monkeypatch.setattr("app.election_phase.election_today", lambda: date(2026, 9, 29))
        data = _body(elections.pvi_map(db_session))
        assert data["electionDay"] == "2026-11-03"

    def test_election_day_stays_on_the_election_just_held(self, db_session, monkeypatch):
        """While its results are on show the page is about the election just
        held (election_phase), not already the next one two years out."""
        from datetime import date

        monkeypatch.setattr("app.election_phase.election_today", lambda: date(2026, 11, 6))
        data = _body(elections.pvi_map(db_session))
        assert data["electionDay"] == "2026-11-03"


class TestUnopposedNomineesAreNotTreatedAsLosers:
    """A state that cancels an uncontested primary publishes no row for a
    candidate who drew no opponent, so they never get confirmed_general
    — and the defeated-filer filter then dropped them as if they had
    lost. Measured against real 2026 production data on 2026-09-23: 36
    real candidates deleted from live races, 19 of them SITTING members
    of Congress running for re-election (Warner and Ernst in their own
    Senate races; Crockett, Himes, Castor, Griffith, Bilirakis and a
    dozen more in theirs). Those pages showed a one-party ballot."""

    def test_sole_filer_of_an_unconfirmed_party_comes_back(self, db_session):
        """Brian Mast's shape: the other party confirmed a nominee, his
        own primary was uncontested so the file never listed him, and he
        is the only Republican in the race."""
        _race(db_session, "2026-HOUSE-FL-21", "FL", office="H", district=21)
        _candidate(db_session, "DNOM", "2026-HOUSE-FL-21", "MARTIN, JAMES",
                   party="DEM", confirmed_general=True)
        _candidate(db_session, "MAST", "2026-HOUSE-FL-21", "MAST, BRIAN", party="REP")
        db_session.commit()

        race = db_session.query(Race).filter(Race.id == "2026-HOUSE-FL-21").first()
        assert sorted(c.id for c in elections._confirmed_or_all(race.candidates, "FL", False)) == ["DNOM", "MAST"]

    def test_the_single_fec_incumbent_comes_back_from_a_crowded_party(self, db_session):
        """Mark Warner's shape: several Democrats hold FEC filings, so
        "sole filer" cannot save him — but exactly one carries FEC's
        incumbent coding, the same single-I condition _stale_incumbent_ids
        already requires before trusting that field."""
        _race(db_session, "2026-SEN-VA", "VA")
        _candidate(db_session, "RNOM", "2026-SEN-VA", "MIZUSAWA, BERT",
                   party="REP", confirmed_general=True)
        _candidate(db_session, "WARNER", "2026-SEN-VA", "WARNER, MARK",
                   party="DEM", incumbent_challenge="I")
        _candidate(db_session, "PAPER", "2026-SEN-VA", "NOBODY, A", party="DEM")
        db_session.commit()

        race = db_session.query(Race).filter(Race.id == "2026-SEN-VA").first()
        got = sorted(c.id for c in elections._confirmed_or_all(race.candidates, "VA", False))
        assert got == ["RNOM", "WARNER"]

    def test_a_crowded_party_with_no_incumbent_is_not_guessed_at(self, db_session):
        """A genuine coverage gap in the state's own feed. Inventing a
        nominee from several equally-plausible filers would be worse than
        the gap, so nothing comes back."""
        _race(db_session, "2026-SEN-VA", "VA")
        _candidate(db_session, "RNOM", "2026-SEN-VA", "MIZUSAWA, BERT",
                   party="REP", confirmed_general=True)
        _candidate(db_session, "D1", "2026-SEN-VA", "ONE, A", party="DEM")
        _candidate(db_session, "D2", "2026-SEN-VA", "TWO, B", party="DEM")
        db_session.commit()

        race = db_session.query(Race).filter(Race.id == "2026-SEN-VA").first()
        assert [c.id for c in elections._confirmed_or_all(race.candidates, "VA", False)] == ["RNOM"]

    def test_independents_and_minor_parties_never_come_back(self, db_session):
        """They qualify by petition, not by primary, so a primary file
        structurally cannot see them — which is what candidateSource's
        "nominees" already discloses. Re-admitting every such filer was
        the naive version of this fix: 540 candidates instead of 36, 118
        of them independents."""
        _race(db_session, "2026-SEN-VA", "VA")
        _candidate(db_session, "DNOM", "2026-SEN-VA", "REAL, D", party="DEM", confirmed_general=True)
        _candidate(db_session, "RNOM", "2026-SEN-VA", "REAL, R", party="REP", confirmed_general=True)
        _candidate(db_session, "IND", "2026-SEN-VA", "SOLO, I", party="IND")
        _candidate(db_session, "LIB", "2026-SEN-VA", "FREE, L", party="LIB")
        db_session.commit()

        race = db_session.query(Race).filter(Race.id == "2026-SEN-VA").first()
        assert sorted(c.id for c in elections._confirmed_or_all(race.candidates, "VA", False)) == ["DNOM", "RNOM"]

    def test_a_top_four_state_is_left_alone_entirely(self, db_session):
        """Alaska's single combined contest really does decide every
        advancer regardless of party, so a party with nobody confirmed
        genuinely has nobody — this relaxation must not touch it."""
        _race(db_session, "2026-SEN-AK", "AK")
        _candidate(db_session, "A1", "2026-SEN-AK", "PELTOLA, MARY", party="DEM", confirmed_general=True)
        _candidate(db_session, "OUT", "2026-SEN-AK", "SOMEONE, R",
                   party="REP", incumbent_challenge="I")
        db_session.commit()

        race = db_session.query(Race).filter(Race.id == "2026-SEN-AK").first()
        assert [c.id for c in elections._confirmed_or_all(race.candidates, "AK", False)] == ["A1"]

    def test_an_incumbent_who_lost_their_primary_stays_filtered(self, db_session):
        """The regression this must not cause. Losing a primary implies
        the primary happened, which implies that party HAS a confirmed
        nominee, which excludes the party from re-admission entirely."""
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "WINNER", "2026-SEN-GA", "CHALLENGER, A",
                   party="REP", confirmed_general=True)
        _candidate(db_session, "BEATEN", "2026-SEN-GA", "INCUMBENT, B",
                   party="REP", incumbent_challenge="I")
        db_session.commit()

        race = db_session.query(Race).filter(Race.id == "2026-SEN-GA").first()
        assert [c.id for c in elections._confirmed_or_all(race.candidates, "GA", False)] == ["WINNER"]

    def test_the_payload_marks_which_candidates_are_actually_confirmed(self, db_session):
        """A mixed list must not present the recovered candidate as a
        state-confirmed nominee — candidateSource is per-race and cannot
        carry this."""
        _race(db_session, "2026-HOUSE-GA-11", "GA", office="H", district=11)
        _candidate(db_session, "DNOM", "2026-HOUSE-GA-11", "MARTIN, JAMES",
                   party="DEM", confirmed_general=True)
        _candidate(db_session, "MAST", "2026-HOUSE-GA-11", "MAST, BRIAN", party="REP")
        db_session.commit()

        data = _body(elections.race_detail("2026-HOUSE-GA-11", db_session))
        by_id = {c["id"]: c for c in data["candidates"]}
        assert by_id["DNOM"]["confirmed"] is True
        assert by_id["MAST"]["confirmed"] is False


class TestCoverageFeedShowsOnlyVettedSources:
    """2026-09-23: Connecticut's ballot page served a tabloid item about a
    diver's death, five reposts of one YouTube video, a bill bot and a
    Spanish health-tip post — 74 items, 5 of them journalism. The Bluesky
    half of this feed is an open keyword search for a candidate's name,
    and a name-mention is not coverage."""

    def _item(self, db, race_id, source_type, title, url, **kw):
        it = RaceCoverageItem(
            race_id=race_id, source_type=source_type,
            source_name=kw.pop("source_name", "src"),
            title=title, url=url, **kw,
        )
        db.add(it)
        return it

    def test_social_noise_is_not_served_as_race_coverage(self, db_session):
        _race(db_session, "2026-HOUSE-CT-3", "CT", office="H", district=3)
        self._item(db_session, "2026-HOUSE-CT-3", "news",
                   "Larson loses to younger primary challenger", "u1")
        self._item(db_session, "2026-HOUSE-CT-3", "bluesky",
                   "The body of experienced diver Andrew Rice, 43, was found", "u2")
        db_session.commit()

        data = _body(elections.race_detail("2026-HOUSE-CT-3", db_session))
        titles = [c["title"] for c in data["coverage"]]
        assert titles == ["Larson loses to younger primary challenger"]

    def test_the_state_feed_applies_the_same_rule(self, db_session):
        _race(db_session, "2026-SEN-CT", "CT")
        self._item(db_session, "2026-SEN-CT", "news", "Real reporting", "n1")
        self._item(db_session, "2026-SEN-CT", "bluesky", "Reservoir Dogs 4K on sale", "b1")
        db_session.commit()

        data = _body(elections.state_ballot("CT", db_session))
        assert [c["title"] for c in data["coverage"]] == ["Real reporting"]

    @pytest.mark.parametrize("race_id, state, office, district, title, item_kw", [
        # "Elect Jonathan Nez to Congress!" scores 0.632 — campaign material
        # is maximally on-topic for a campaign, so relevance alone would
        # admit exactly what a non-partisan platform must not carry.
        pytest.param("2026-HOUSE-AZ-2", "AZ", "H", 2, "Elect Jonathan Nez to Congress!",
                     dict(relevance=0.632, has_advocacy=True), id="relevant_advocacy"),
        pytest.param("2026-HOUSE-NJ-7", "NJ", "H", 7, "Reservoir Dogs 4K (iTunes) C$4.99",
                     dict(relevance=0.111, has_advocacy=False), id="irrelevant"),
        # Fail closed: NULL relevance means never scored, and the next
        # ingest fills it in.
        pytest.param("2026-SEN-CT", "CT", "S", None, "Unscored post", {}, id="unscored"),
        # A DNS-verified domain handle is not enough: @crowbar.wtf is a
        # domain, and it published "Dave Hughes still a whiny cunt" onto
        # Minnesota's page. Relevance and no-advocacy both passed it — it IS
        # about the race and it never says "vote for".
        pytest.param("2026-HOUSE-MN-7", "MN", "H", 7, "Dave Hughes still a whiny cunt.",
                     dict(relevance=0.62, has_advocacy=False, source_name="@crowbar.wtf"),
                     id="clean_looking_domain_handle"),
        # Measured over 1,200 real items: of 377 that cleared relevance and
        # the no-advocacy bar, the 316 on *.bsky.social were "Jon Husted Is
        # For Sale", "Awww poor Cindy :-(", a Celtic football post — and the
        # opponent's own campaign account attacking him.
        pytest.param("2026-SEN-OH", "OH", "S", None, "Jon Husted doesn't give a damn about working people",
                     dict(relevance=0.55, has_advocacy=False, source_name="@sherrodbrownoh.bsky.social"),
                     id="default_bsky_handle"),
    ])
    def test_no_social_item_is_shown_however_it_scores(
        self, db_session, race_id, state, office, district, title, item_kw,
    ):
        _race(db_session, race_id, state, office=office, district=district)
        self._item(db_session, race_id, "bluesky", title, "b", **item_kw)
        db_session.commit()
        data = _body(elections.state_ballot(state, db_session))
        assert data["coverage"] == []



class TestLiveResults:
    """GET /elections/results — the count, the updates feed, coverage."""

    @staticmethod
    def _results_window():
        from datetime import date
        from unittest.mock import patch

        return patch("app.election_phase.election_today", return_value=date(2026, 11, 4))

    def _seed(self, db_session):
        import json as _json

        from app.models import ElectionResultEvent, RaceResult

        _race(db_session, "2026-HOUSE-GA-2", "GA", "H", 2)
        _race(db_session, "2026-SEN-CO", "CO", "S", None)
        db_session.add(RaceResult(
            race_id="2026-HOUSE-GA-2", election_date="2026-11-03", source_name="GA SOS",
            source_url="https://results.example/ga",
            tallies=_json.dumps([{"name": "Ray Jones", "party": "REP", "votes": 600, "candidateId": None},
                                 {"name": "Dana Smith", "party": "DEM", "votes": 400, "candidateId": "H1"}]),
            votes_counted=1000, reporting_units=70, total_units=100, held_by_party="DEM",
            flip_announced=True,
        ))
        db_session.add(ElectionResultEvent(race_id="2026-HOUSE-GA-2", election_date="2026-11-03",
                                           kind="flip", detail="{}"))
        db_session.flush()

    def test_campaign_phase_is_empty(self, db_session):
        from datetime import date
        from unittest.mock import patch

        self._seed(db_session)
        with patch("app.election_phase.election_today", return_value=date(2026, 10, 1)):
            data = _body(elections.live_results(None, db_session))
        assert data["phase"]["phase"] == "campaign"
        assert data["races"] == [] and data["updates"] == []

    def test_results_phase_returns_the_count(self, db_session):
        self._seed(db_session)
        with self._results_window():
            data = _body(elections.live_results(None, db_session))
        assert data["phase"]["phase"] == "results"
        [race] = data["races"]
        assert race["leaderParty"] == "REP" and race["flip"] is True
        assert race["candidates"][0] == {"name": "Ray Jones", "party": "REP", "votes": 600, "pct": 60.0, "candidateId": None}
        assert data["updates"][0]["kind"] == "flip"
        assert "GA" in data["liveStates"] and "TX" not in data["liveStates"]
        assert data["senateStates"] == ["CO"]
        assert "UT" in data["redrawnStates"] and "MO" not in data["redrawnStates"]
        assert data["pollsClose"]["GA"] == "2026-11-04T00:00:00Z"  # 7 PM ET

    def test_senate_races_lists_each_seat_a_state_elects(self, db_session):
        """A state electing both its senators lists both races, counted or
        not, so the map can say the uncounted one has no count — and the
        list is the cycle's, outside the results window too."""
        from datetime import date
        from unittest.mock import patch

        self._seed(db_session)
        special = _race(db_session, "2026-SEN-OH-SPECIAL", "OH", "S")
        special.is_special = True
        _race(db_session, "2026-SEN-OH", "OH", "S")
        _race(db_session, "2024-SEN-TX", "TX", "S", cycle_year=2024)
        db_session.flush()
        with self._results_window():
            data = _body(elections.live_results(None, db_session))
        assert data["senateRaces"] == {
            "CO": [{"raceId": "2026-SEN-CO", "isSpecial": False}],
            "OH": [{"raceId": "2026-SEN-OH", "isSpecial": False},
                   {"raceId": "2026-SEN-OH-SPECIAL", "isSpecial": True}],
        }
        assert data["senateStates"] == ["CO", "OH"]
        with patch("app.election_phase.election_today", return_value=date(2026, 10, 1)):
            data = _body(elections.live_results("CO", db_session))
        assert set(data["senateRaces"]) == {"CO", "OH"}

    def test_flip_is_what_the_sync_announced_not_the_bar_right_now(self, db_session):
        """An announced flip whose count lost its reporting figures stays
        marked (the issue and posts keep it too); a count that clears the
        bar but hasn't been announced yet isn't marked."""
        from app.models import RaceResult

        self._seed(db_session)
        row = db_session.get(RaceResult, "2026-HOUSE-GA-2")
        row.reporting_units = row.total_units = None  # no longer qualifies
        db_session.flush()
        with self._results_window():
            [race] = _body(elections.live_results(None, db_session))["races"]
        assert race["flip"] is True
        row.reporting_units, row.total_units, row.flip_announced = 100, 100, None  # qualifies, never said
        db_session.flush()
        with self._results_window():
            [race] = _body(elections.live_results(None, db_session))["races"]
        assert race["flip"] is False

    def test_an_announced_flip_stays_marked_through_a_held_poll(self, db_session):
        """A poll whose votes-counted fell is stored but announces nothing —
        no FLIP_REVERSED, and the Action Center issue isn't touched — so the
        page keeps saying what the issue and the feed say until the next
        poll reverts all three together (test_election_results_sync's
        TestHeldPollAgreement runs that through the sync)."""
        import json as _json

        from app.models import RaceResult

        self._seed(db_session)
        row = db_session.get(RaceResult, "2026-HOUSE-GA-2")
        row.tallies = _json.dumps([{"name": "Dana Smith", "party": "DEM", "votes": 500, "candidateId": "H1"},
                                   {"name": "Ray Jones", "party": "REP", "votes": 450, "candidateId": None}])
        row.votes_counted = 950
        db_session.flush()
        with self._results_window():
            data = _body(elections.live_results(None, db_session))
        [race] = data["races"]
        assert race["leaderParty"] == race["heldBy"] == "DEM"
        assert race["flip"] is True
        assert data["updates"][0]["kind"] == "flip"
        row.flip_announced = False  # the next poll's FLIP_REVERSED
        db_session.flush()
        with self._results_window():
            [race] = _body(elections.live_results(None, db_session))["races"]
        assert race["flip"] is False

    def test_says_how_each_feed_read_went(self, db_session):
        from datetime import datetime

        from app.models import LiveResultRead

        db_session.add(LiveResultRead(state="CO", election_date="2026-11-03", status="untrusted",
                                      checked_at=datetime(2026, 11, 4, 3)))
        db_session.add(LiveResultRead(state="GA", election_date="2026-11-03", status="ok",
                                      checked_at=datetime(2026, 11, 4, 3), last_ok_at=datetime(2026, 11, 4, 3)))
        db_session.flush()
        with self._results_window():
            data = _body(elections.live_results(None, db_session))
            only_co = _body(elections.live_results("CO", db_session))
        assert data["feeds"]["CO"] == {"status": "untrusted", "checkedAt": "2026-11-04T03:00:00Z", "lastOkAt": None}
        assert data["feeds"]["GA"]["lastOkAt"] == "2026-11-04T03:00:00Z"
        assert set(only_co["feeds"]) == {"CO"}

    def test_filters_by_state(self, db_session):
        self._seed(db_session)
        with self._results_window():
            assert _body(elections.live_results("co", db_session))["races"] == []
            assert len(_body(elections.live_results("GA", db_session))["races"]) == 1

    def test_short_cache(self, db_session):
        with self._results_window():
            response = elections.live_results(None, db_session)
        assert "max-age=30" in response.headers["Cache-Control"]


class TestPhaseCacheLifetime:
    """Responses carrying the election's phase or date (the state ballot's
    `phase`, the PVI map's `electionDay`) are cached for the live count's
    lifetime around election day, so the switch to election-day mode
    doesn't reach readers ten minutes late."""

    def test_short_from_the_day_before_through_the_results_window(self):
        from datetime import date

        from app.election_phase import resolve_active_election

        def at(day):
            election = resolve_active_election(day, lambda _: None)
            return elections.phase_cache_s(election, day)

        assert at(date(2026, 10, 1)) == elections.CACHE_TTL_LIST_S
        assert at(date(2026, 11, 1)) == elections.CACHE_TTL_LIST_S
        assert at(date(2026, 11, 2)) == elections.CACHE_TTL_RESULTS_S  # the day before
        assert at(date(2026, 11, 3)) == elections.CACHE_TTL_RESULTS_S
        assert at(date(2026, 11, 17)) == elections.CACHE_TTL_RESULTS_S  # the window's last day
        assert at(date(2026, 11, 18)) == elections.CACHE_TTL_LIST_S

    def test_the_pvi_map_uses_it(self, db_session):
        from datetime import date
        from unittest.mock import patch

        with patch("app.election_phase.election_today", return_value=date(2026, 11, 3)), \
                patch("app.api.elections.election_today", return_value=date(2026, 11, 3)):
            response = elections.pvi_map(db_session)
        assert f"max-age={elections.CACHE_TTL_RESULTS_S}," in response.headers["Cache-Control"]

    def test_the_race_list_uses_it(self, db_session):
        """Its cycle turns over when the results window closes: the long
        list lifetime kept serving the held election's races after."""
        from datetime import date
        from unittest.mock import patch

        for day, ttl in ((date(2026, 11, 10), elections.CACHE_TTL_RESULTS_S),
                         (date(2026, 10, 1), elections.CACHE_TTL_LIST_S)):
            with patch("app.election_phase.election_today", return_value=day), \
                    patch("app.api.elections.election_today", return_value=day):
                response = elections.list_races(db_session)
            assert f"max-age={ttl}," in response.headers["Cache-Control"]
