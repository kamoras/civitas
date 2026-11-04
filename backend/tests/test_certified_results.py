"""Certified results: the recount rule (app/recount_rules.py), the
certified count's storage and announcement, and the sworn-in floor
(live_results/certified.py)."""

import asyncio
import json
from datetime import date, datetime


from app import recount_rules as rr
from app.live_results import certified as cr
from app.models import (
    Candidate, ElectionResultEvent, HousePipelineRun, PipelineStatus, Race, RaceCertification, Representative,
)
from app.pipeline.fetch.election_results import ContestCount, StateCount

DAY = date(2026, 11, 3)


def _outcome(state, votes, certified_on, today, office="H", district=2, total=None):
    return rr.outcome(state, office, district, votes, total or sum(votes), DAY, certified_on, today)


class TestRecountRule:
    def test_every_state_and_dc_has_a_rule_citing_a_statute(self):
        rules = json.loads(rr._PATH.read_text())["states"]
        assert len(rules) == 51
        assert all(rule.get("statute") for rule in rules.values())

    def test_a_margin_inside_the_automatic_recount_waits_for_the_new_congress(self):
        # Florida: 0.5% or less is recounted with no request.
        found = _outcome("FL", [50_200, 49_800], date(2026, 11, 17), date(2026, 12, 20))
        assert found.status == rr.RECOUNT and found.until is None

    def test_a_wide_margin_with_no_recount_right_is_certified_at_once(self):
        found = _outcome("FL", [60_000, 40_000], date(2026, 11, 17), date(2026, 11, 17))
        assert found.status == rr.CERTIFIED

    def test_a_requested_recount_window_holds_the_winner_until_it_closes(self):
        # Maryland: any candidate may ask within 3 days after certification.
        certified_on = date(2026, 12, 4)
        assert _outcome("MD", [60_000, 40_000], certified_on, date(2026, 12, 7)) == rr.Outcome(
            rr.RECOUNT_POSSIBLE, until=date(2026, 12, 7), statute="Md. Election Law 12-101")
        assert _outcome("MD", [60_000, 40_000], certified_on, date(2026, 12, 8)).status == rr.CERTIFIED

    def test_a_margin_outside_the_request_threshold_needs_no_wait(self):
        # Virginia: a recount may be asked for only within 1%.
        assert _outcome("VA", [55_000, 45_000], date(2026, 12, 7), date(2026, 12, 7)).status == rr.CERTIFIED
        assert _outcome("VA", [50_400, 49_600], date(2026, 12, 7), date(2026, 12, 7)).status == rr.RECOUNT_POSSIBLE

    def test_business_days_skip_weekends_and_thanksgiving(self):
        # Georgia: two business days after certification. Certified Monday
        # Nov 23 2026: Tue 24, then Wed 25 (Thanksgiving Thursday and the
        # Friday after skipped) -> Wed 25.
        assert rr.deadline({"days": 2, "after": "certification", "business": True}, DAY,
                           date(2026, 11, 23)) == date(2026, 11, 25)
        # Certified Wednesday Nov 25: Mon 30 and Tue Dec 1.
        assert rr.deadline({"days": 2, "after": "certification", "business": True}, DAY,
                           date(2026, 11, 25)) == date(2026, 12, 1)

    def test_a_deadline_counted_from_the_election_never_ends_before_certification(self):
        # Kentucky: within 10 days of the election, certified later.
        found = _outcome("KY", [60_000, 40_000], date(2026, 11, 23), date(2026, 11, 23))
        assert found.status == rr.RECOUNT_POSSIBLE and found.until == date(2026, 11, 23)

    def test_no_fixed_deadline_waits_for_the_new_congress(self):
        found = _outcome("OK", [70_000, 30_000], date(2026, 11, 10), date(2026, 12, 30))
        assert found.status == rr.RECOUNT_POSSIBLE and found.until is None

    def test_georgia_needs_a_majority(self):
        found = _outcome("GA", [49_000, 45_000, 6_000], date(2026, 11, 20), date(2026, 12, 30))
        assert found.status == rr.RUNOFF

    def test_a_runoff_rule_limited_to_one_office_leaves_the_other_alone(self):
        # Louisiana's 2026 runoff covers its House contests only.
        assert _outcome("LA", [45_000, 40_000, 15_000], date(2026, 11, 17), date(2026, 12, 30)).status == rr.RUNOFF
        assert _outcome("LA", [45_000, 40_000, 15_000], date(2026, 11, 17), date(2026, 12, 30),
                        office="S", district=None).status == rr.CERTIFIED

    def test_a_tie_is_never_a_winner(self):
        assert _outcome("TN", [50_000, 50_000], date(2026, 11, 20), date(2026, 12, 30)).status == rr.RECOUNT

    def test_the_margin_is_measured_against_every_vote_cast(self):
        # Nebraska's automatic recount is 1% of the leader's votes; read
        # against every vote cast, a margin of 1,100 in 120,000 (0.92%) is
        # inside it, so the race waits.
        found = _outcome("NE", [55_550, 54_450], date(2026, 11, 30), date(2026, 12, 30), total=120_000)
        assert found.status == rr.RECOUNT


def _race(db):
    race = Race(id="2026-HOUSE-MD-2", cycle_year=2026, office="H", state="MD", district=2)
    db.add(race)
    db.add(Candidate(id="H6MD02001", race_id=race.id, name="SMITH, DANA", party="DEM", confirmed_general=True))
    db.add(Candidate(id="H6MD02002", race_id=race.id, name="JONES, RAY", party="REP", confirmed_general=True))
    db.flush()
    return race


def _certified(certified_on, d=60_000, r=40_000):
    return StateCount(source_name="Maryland State Board of Elections", page_url="https://elections.example/md",
                      official=True, certified_on=certified_on,
                      contests=[ContestCount(office="H", district=2,
                                             candidates=[("Dana Smith", "D", d), ("Ray Jones", "R", r)])])


def _events(db):
    return db.query(ElectionResultEvent).filter(ElectionResultEvent.kind == cr.CERTIFIED_EVENT).all()


class TestCertifiedSync:
    def test_stored_then_announced_once_the_recount_window_closes(self, db_session):
        race = _race(db_session)
        cr.apply_certified(db_session, "MD", DAY, _certified(date(2026, 12, 4)), date(2026, 12, 4))
        row = db_session.get(RaceCertification, race.id)
        view = cr.certification_json(row, date(2026, 12, 4))
        assert view["status"] == rr.RECOUNT_POSSIBLE and view["recountUntil"] == "2026-12-07"
        assert view["winner"] is None and _events(db_session) == []
        assert [c["candidateId"] for c in view["candidates"]] == ["H6MD02001", "H6MD02002"]

        view = cr.certification_json(row, date(2026, 12, 8))
        assert view["status"] == rr.CERTIFIED and view["winner"]["name"]
        assert cr._announce(db_session, row, date(2026, 12, 8)) is not None
        assert cr._announce(db_session, row, date(2026, 12, 9)) is None  # said once

    def test_a_recertified_count_moves_its_change_time(self, db_session, freeze_utcnow):
        race = _race(db_session)
        freeze_utcnow(datetime(2026, 12, 4, 12))
        cr.apply_certified(db_session, "MD", DAY, _certified(date(2026, 12, 4)), date(2026, 12, 4))
        first = db_session.get(RaceCertification, race.id).changed_at
        freeze_utcnow(datetime(2026, 12, 20, 12))
        cr.apply_certified(db_session, "MD", DAY, _certified(date(2026, 12, 4)), date(2026, 12, 20))
        assert db_session.get(RaceCertification, race.id).changed_at == first
        cr.apply_certified(db_session, "MD", DAY, _certified(date(2026, 12, 18), d=59_990), date(2026, 12, 20))
        assert db_session.get(RaceCertification, race.id).changed_at == datetime(2026, 12, 20, 12)

    def test_a_state_not_yet_certified_stores_nothing(self, db_session, monkeypatch):
        _race(db_session)

        async def not_yet(client, state, day):
            return None

        monkeypatch.setattr(cr, "certified_states", lambda: {"MD"})
        monkeypatch.setattr(cr, "fetch_certified_count", not_yet)
        summary = asyncio.run(cr.sync_certified(db_session, None, DAY, date(2026, 11, 20)))
        assert summary["MD"] == {"status": "not_certified"}
        assert db_session.query(RaceCertification).count() == 0

    def test_certification_is_due_from_the_day_after_to_a_month_past_january_3(self):
        assert not cr.certification_due(DAY, DAY)
        assert cr.certification_due(DAY, date(2026, 11, 4))
        assert cr.certification_due(DAY, date(2027, 2, 2))
        assert not cr.certification_due(DAY, date(2027, 2, 3))


class TestSwornIn:
    def _seat(self, db, sworn="2027-01-03", name="Dana Smith"):
        race = _race(db)
        db.add(Representative(id="dana-smith", name=name, state="MD", district=2, party="D", is_current=True,
                              sworn_date=sworn, bioguide_id="S000001"))
        db.flush()
        return race

    def _house_run(self, db, completed):
        db.add(HousePipelineRun(started_at=completed, completed_at=completed, status=PipelineStatus.COMPLETED))
        db.flush()

    def test_recorded_once_the_roster_is_read_after_the_congress_convenes(self, db_session, freeze_utcnow):
        race = self._seat(db_session)
        freeze_utcnow(datetime(2027, 1, 4, 12))
        self._house_run(db_session, datetime(2027, 1, 3, 12))  # before noon Eastern
        assert cr.record_sworn_in(db_session, DAY) == 0
        self._house_run(db_session, datetime(2027, 1, 4, 3))
        assert cr.record_sworn_in(db_session, DAY) == 1
        view = cr.certification_json(db_session.get(RaceCertification, race.id), date(2027, 1, 4))
        assert view["status"] == cr.SWORN_IN and view["winner"]["name"] == "Dana Smith"

    def test_a_member_sworn_in_to_the_last_congress_is_not_the_winner(self, db_session, freeze_utcnow):
        self._seat(db_session, sworn="2025-01-03")
        freeze_utcnow(datetime(2027, 1, 5, 12))
        self._house_run(db_session, datetime(2027, 1, 4, 3))
        assert cr.record_sworn_in(db_session, DAY) == 0

    def test_a_member_who_was_not_on_the_ballot_is_not_recorded(self, db_session, freeze_utcnow):
        self._seat(db_session, name="Pat Other")
        freeze_utcnow(datetime(2027, 1, 5, 12))
        self._house_run(db_session, datetime(2027, 1, 4, 3))
        assert cr.record_sworn_in(db_session, DAY) == 0


class TestKentuckyReader:
    def test_reads_every_house_contest_of_the_2024_certification(self):
        from pathlib import Path

        from app.pipeline.fetch.state_candidates_ky import parse_general_pages

        fixture = json.loads((Path(__file__).parent / "fixtures" / "certified" / "ky_2024_general_pages.json").read_text())
        contests = parse_general_pages([(p["text"], p["words"]) for p in fixture["pages"]])
        got = {(c.office, c.district): c.candidates for c in contests}
        assert sorted(got) == [("H", n) for n in range(1, 7)]
        assert got[("H", 1)] == [("James R. Comer", "R", 252_729), ("Erin Marshall", "D", 85_524)]
        assert got[("H", 3)][0] == ("Morgan McGarvey", "D", 203_100)
        assert got[("H", 3)][2][1] is None  # a write-in column has no party
        assert got[("H", 5)] == [("Hal Rogers", "R", 261_407)]  # unopposed

    def test_kentucky_is_a_certified_state(self):
        from app.pipeline.fetch.election_results import certified_states

        assert "KY" in certified_states()


def _fixture(name):
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures" / "certified" / name).read_text())


class TestWyomingReader:
    def test_reads_the_2024_certified_general(self):
        from app.pipeline.fetch.state_candidates_wy import general_contests

        contests = {(c.office, c.district): c for c in general_contests(_fixture("wy_2024_general_rows.json")["rows"],
                                                                         date(2024, 11, 5))}
        assert sorted(contests) == [("H", None), ("S", None)]  # the President's contest is no federal seat here
        senate = contests[("S", None)]
        assert senate.candidates == [("John Barrasso", "R", 198_418), ("Scott D Morrow", "D", 63_727)]
        assert senate.total_votes == 198_418 + 63_727 + 2_017  # write-ins count; over- and undervotes don't
        assert [p for _, p, _ in contests[("H", None)].candidates][:3] == ["R", "D", "L"]

    def test_another_election_s_workbook_is_refused(self):
        from app.pipeline.fetch.state_candidates_wy import general_contests

        assert general_contests(_fixture("wy_2024_general_rows.json")["rows"], date(2026, 11, 3)) is None


class TestOregonReader:
    def test_reads_every_district_of_the_2024_abstract(self):
        from app.pipeline.fetch.state_candidates_or import general_contests

        pages = [(p["text"], p["lines"]) for p in _fixture("or_2024_general_pages.json")["pages"]]
        contests = {(c.office, c.district): c for c in general_contests(pages)}
        assert sorted(contests) == [("H", n) for n in range(1, 7)]  # two districts to a page
        first = contests[("H", 1)]
        assert first.candidates[0] == ("Suzanne Bonamici", "D", 241_556)
        assert first.total_votes == 241_556 + 98_908 + 10_840 + 687  # "Misc." write-ins count
        assert ("Lori Chavez-DeRemer", "R", 180_420) in contests[("H", 5)].candidates


class TestMassachusettsReader:
    def test_reads_the_2024_senate_and_a_house_race(self):
        from app.pipeline.fetch.state_candidates_ma import general_contest

        pages = _fixture("ma_2024_general_pages.json")
        senate = general_contest(pages["senate"], 2024, "S")
        assert senate.candidates == [("Elizabeth Ann Warren", "D", 2_041_668), ("John Deaton", "R", 1_365_440)]
        assert senate.total_votes == 2_041_668 + 1_365_440 + 6_221  # All Others counts; blanks don't
        house = general_contest(pages["house_8"], 2024, "H")
        assert house.district == 8 and house.candidates[0] == ("Stephen F. Lynch", "D", 265_432)

    def test_a_page_for_another_year_or_office_is_refused(self):
        from app.pipeline.fetch.state_candidates_ma import general_contest

        pages = _fixture("ma_2024_general_pages.json")
        assert general_contest(pages["senate"], 2026, "S") is None
        assert general_contest(pages["senate"], 2024, "H") is None


class TestEnhancedVotingOfficial:
    async def test_a_count_is_certified_only_once_the_portal_marks_it_official(self, monkeypatch):
        from app.pipeline.fetch import election_results as er

        counts = []

        async def live(client, day, state, source):
            return counts[-1]

        monkeypatch.setattr(er, "_readers", lambda: {"enhanced_voting": live})
        source = {"source_name": "Rhode Island Board of Elections", "strategy": "enhanced_voting",
                  "certified_results": {"strategy": "enhanced_voting_official"}}
        monkeypatch.setattr(er, "source_for_state", lambda state: source)
        counts.append(StateCount(source_name="RI", page_url=None, official=False))
        assert await er._enhanced_voting_official(None, DAY, "RI", {}) is None
        counts.append(StateCount(source_name="RI", page_url=None, official=True))
        assert (await er._enhanced_voting_official(None, DAY, "RI", {})).official is True


class TestNewJerseyReader:
    def test_reads_the_2024_official_list_across_a_page_break(self):
        from app.pipeline.fetch.state_candidates_nj import general_contests

        contests = {c.district: c for c in general_contests(_fixture("nj_2024_general_house_pages.json")["pages"])}
        assert contests[1].candidates[0] == ("DONALD NORCROSS", "D", 208_808)
        assert contests[1].total_votes == 361_060  # the district's own Total line
        # The 12th's last candidate is printed again on the next page; the
        # Total after that is the district's, not a second one for him.
        twelfth = contests[12]
        assert [n for n, _, _ in twelfth.candidates].count("VIC KAPLAN") == 1
        assert twelfth.total_votes == 321_660


class TestCaliforniaReader:
    def test_reads_the_2024_statement_of_vote_and_its_certificate(self):
        from app.pipeline.fetch.state_candidates_ca_sov import certified_on, page_slug, workbook_contests

        sov = _fixture("ca_2024_general_sov.json")
        house = workbook_contests(sov["house"], "H", False)
        assert [c.district for c in house] == [1, 2]
        assert house[0].candidates == [("Rose Penelope Yee", "D", 110_636), ("Doug LaMalfa", "R", 208_592)]
        senate = workbook_contests(sov["senate_full_term"], "S", False)
        assert senate[0].candidates[0] == ("Adam B. Schiff", "D", 9_036_252) and not senate[0].is_special
        assert certified_on(sov["certificate"]) == date(2024, 12, 13)
        assert page_slug(date(2026, 11, 3)) == "general-election-nov-3-2026"


class TestIndianaReader:
    async def test_the_count_is_read_live_and_certified_only_once_flagged(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_in as ind

        races = _fixture("in_2026_general_races.json")["races"]
        settings = {"CurrentElection": "11/03/2026", "ElectionType": "G", "Certified": "F",
                    "VersionType": "A", "VersionCode": "20261103_210451"}
        files = {
            "settings.json": settings,
            "statewideElectionsC_A.json": {"List": [{"Heading": "Federal", "Items": {"Item": [{"OFFICECATEGORYID": "1005"}]}}]},
            "OffCatC_1005_A.json": {"StatewideSummary": {"Race": races}},
        }

        async def get(client, url, label):
            return files[url.rsplit("/", 1)[-1]]

        monkeypatch.setattr(ind, "_get_json", get)
        live = await ind.fetch_general_results(None, DAY, "IN", {})
        assert [(c.office, c.district) for c in live.contests] == [("H", 1), ("H", 2)]
        assert live.contests[0].candidates[0] == ("Frank J. Mrvan", "D", 47_443)
        assert live.official is False and live.source_version == "11/03/2026:20261103210451"
        assert await ind.fetch_certified_general(None, DAY, "IN", {}) is None
        settings["Certified"] = "T"
        assert (await ind.fetch_certified_general(None, DAY, "IN", {})).official is True
        # Another election on the site is not this one.
        assert await ind.fetch_general_results(None, date(2024, 11, 5), "IN", {}) is None
