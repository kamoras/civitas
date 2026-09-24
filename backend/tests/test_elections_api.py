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


def _body(response):
    return json.loads(response.body)


def _race(db, race_id, state, office="S", district=None, cycle_year=2026):
    r = Race(id=race_id, cycle_year=cycle_year, office=office, state=state, district=district)
    db.add(r)
    return r


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

    def test_house_race_uses_district_pvi_not_state_pvi(self, db_session):
        _race(db_session, "2026-HOUSE-CA-12", "CA", office="H", district=12)
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["pvi"] == elections.get_district_pvi_map()["CA-12"]
        # The provenance flag tells the frontend which map the number came
        # from — a district figure, not the statewide fallback.
        assert data[0]["pviLevel"] == "district"

    def test_senate_race_pvi_is_flagged_as_state_level(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        db_session.commit()

        data = _body(elections.list_races(db_session))
        assert data[0]["pviLevel"] == "state"

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


class TestCandidateDetail:
    def test_404_for_unknown_candidate(self, db_session):
        with pytest.raises(HTTPException) as exc_info:
            elections.candidate_detail("nonexistent", db_session)
        assert exc_info.value.status_code == 404

    def test_returns_candidate_with_parent_race(self, db_session):
        _race(db_session, "2026-SEN-GA", "GA")
        _candidate(db_session, "S1", "2026-SEN-GA", "OSSOFF, JON", cash_on_hand=500.0)
        db_session.commit()

        data = _body(elections.candidate_detail("S1", db_session))
        assert data["id"] == "S1"
        assert data["cashOnHand"] == 500.0
        assert data["race"]["id"] == "2026-SEN-GA"

    def test_stale_incumbent_flag_is_dropped_here_too(self, db_session):
        """A visitor landing directly on a stale-incumbent's own candidate
        page (see test_elections_state_ballot.py's TestStaleIncumbentFlag
        for the ballot-page half of this) must not see a trustworthy "I"
        either — same correction, same race-mate-shape signal, just
        reached through a different route."""
        _race(db_session, "2026-SEN-MI", "MI")
        _candidate(db_session, "PETERS", "2026-SEN-MI", "PETERS, GARY", incumbent_challenge="I")
        _candidate(db_session, "ROGERS", "2026-SEN-MI", "ROGERS, MICHAEL J", party="REP", incumbent_challenge="O")
        db_session.commit()

        data = _body(elections.candidate_detail("PETERS", db_session))
        assert data["incumbentChallenge"] is None


class TestPviMap:
    def test_returns_both_state_and_district_maps(self):
        data = _body(elections.pvi_map())
        assert "AK" in data["states"]
        assert "AK-0" in data["districts"]

    def test_includes_provenance_metadata(self):
        """The bare numbers over-claim without provenance (2026-07 review
        F7) — the payload must carry per-map source metadata plus the
        lean-is-not-a-forecast note for the frontend to label."""
        data = _body(elections.pvi_map())
        meta = data["meta"]
        assert "states" in meta
        assert "districts" in meta
        assert "not" in meta["note"]  # the "measures lean, not who will win" caveat

    def test_includes_cycle_year(self):
        """Lets /elections's directory page get its header year from the
        same fetch it already makes for map coloring, instead of a
        second fetch of every race."""
        from app.pipeline.election_pipeline import current_election_cycle

        data = _body(elections.pvi_map())
        assert data["cycleYear"] == current_election_cycle()


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
        assert sorted(c.id for c in elections._confirmed_or_all(race.candidates, "FL")) == ["DNOM", "MAST"]

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
        got = sorted(c.id for c in elections._confirmed_or_all(race.candidates, "VA"))
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
        assert [c.id for c in elections._confirmed_or_all(race.candidates, "VA")] == ["RNOM"]

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
        assert sorted(c.id for c in elections._confirmed_or_all(race.candidates, "VA")) == ["DNOM", "RNOM"]

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
        assert [c.id for c in elections._confirmed_or_all(race.candidates, "AK")] == ["A1"]

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
        assert [c.id for c in elections._confirmed_or_all(race.candidates, "GA")] == ["WINNER"]

    def test_the_payload_marks_which_candidates_are_actually_confirmed(self, db_session):
        """A mixed list must not present the recovered candidate as a
        state-confirmed nominee — candidateSource is per-race and cannot
        carry this."""
        _race(db_session, "2026-HOUSE-FL-21", "FL", office="H", district=21)
        _candidate(db_session, "DNOM", "2026-HOUSE-FL-21", "MARTIN, JAMES",
                   party="DEM", confirmed_general=True)
        _candidate(db_session, "MAST", "2026-HOUSE-FL-21", "MAST, BRIAN", party="REP")
        db_session.commit()

        data = _body(elections.race_detail("2026-HOUSE-FL-21", db_session))
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


    def test_a_relevant_ADVOCACY_social_item_is_not_shown(self, db_session):
        """"Elect Jonathan Nez to Congress!" scores 0.632 — campaign
        material is maximally on-topic for a campaign, so relevance alone
        would admit exactly what a non-partisan platform must not carry."""
        _race(db_session, "2026-HOUSE-AZ-2", "AZ", office="H", district=2)
        self._item(db_session, "2026-HOUSE-AZ-2", "bluesky",
                   "Elect Jonathan Nez to Congress!", "b2",
                   relevance=0.632, has_advocacy=True)
        db_session.commit()
        data = _body(elections.state_ballot("AZ", db_session))
        assert data["coverage"] == []

    def test_an_irrelevant_social_item_is_not_shown(self, db_session):
        _race(db_session, "2026-HOUSE-NJ-7", "NJ", office="H", district=7)
        self._item(db_session, "2026-HOUSE-NJ-7", "bluesky",
                   "Reservoir Dogs 4K (iTunes) C$4.99", "b3",
                   relevance=0.111, has_advocacy=False)
        db_session.commit()
        data = _body(elections.state_ballot("NJ", db_session))
        assert data["coverage"] == []

    def test_an_unscored_social_item_is_not_shown(self, db_session):
        """Fail closed: NULL relevance means never scored, and the next
        ingest fills it in."""
        _race(db_session, "2026-SEN-CT", "CT")
        self._item(db_session, "2026-SEN-CT", "bluesky", "Unscored post", "b4")
        db_session.commit()
        data = _body(elections.state_ballot("CT", db_session))
        assert data["coverage"] == []

    def test_no_social_item_is_shown_however_clean_it_looks(self, db_session):
        """A DNS-verified domain handle is not enough: @crowbar.wtf is a
        domain, and it published "Dave Hughes still a whiny cunt" onto
        Minnesota's page. Relevance and no-advocacy both passed it —
        it IS about the race and it never says "vote for"."""
        _race(db_session, "2026-HOUSE-MN-7", "MN", office="H", district=7)
        self._item(db_session, "2026-HOUSE-MN-7", "bluesky",
                   "Dave Hughes still a whiny cunt.", "b7",
                   relevance=0.62, has_advocacy=False, source_name="@crowbar.wtf")
        db_session.commit()
        data = _body(elections.state_ballot("MN", db_session))
        assert data["coverage"] == []

    def test_a_default_bsky_handle_is_not_a_publisher(self, db_session):
        """Measured over 1,200 real items: of 377 that cleared relevance
        and the no-advocacy bar, the 316 on *.bsky.social were "Jon
        Husted Is For Sale", "Awww poor Cindy :-(", a Celtic football
        post — and the opponent's own campaign account attacking him."""
        _race(db_session, "2026-SEN-OH", "OH")
        self._item(db_session, "2026-SEN-OH", "bluesky",
                   "Jon Husted doesn't give a damn about working people", "b5",
                   relevance=0.55, has_advocacy=False,
                   source_name="@sherrodbrownoh.bsky.social")
        db_session.commit()
        data = _body(elections.state_ballot("OH", db_session))
        assert data["coverage"] == []

