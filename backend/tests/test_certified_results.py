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


class TestKansasReader:
    def test_reads_the_2024_official_totals_and_skips_state_contests(self):
        from app.pipeline.fetch.state_candidates_ks import general_contests

        contests = {(c.office, c.district): c for c in general_contests(_fixture("ks_2024_general_lines.json")["lines"])}
        assert sorted(contests) == [("H", n) for n in range(1, 5)]  # the President's and Kansas Senate rows are no seat here
        assert contests[("H", 2)].candidates == [
            ("Derek Schmidt", "R", 172_847), ("Nancy Boyda", "D", 115_685), ("John Hauer", "L", 14_229)]


class TestVermontLiveReader:
    def test_sums_each_candidate_over_the_towns(self):
        from app.pipeline.fetch.state_candidates_vt import general_contests

        (house,) = general_contests(_fixture("vt_2026_general_report.json")["report"])
        assert (house.office, house.district) == ("H", None)
        votes = {name: (party, v) for name, party, v in house.candidates}
        assert votes["BECCA BALINT"] == ("D", 398 + 731)
        assert votes["GERALD MALLOY"] == ("R", 434 + 390)
        # "OTHER WRITE-IN" (cid 0) is in the contest's total and is no one's.
        assert "OTHER WRITE-IN" not in votes
        assert house.total_votes == sum(v for _, v in votes.values()) + 1 + 2


# Each reader's fetch path, end to end against a mocked transport: what it
# asks for, "not published yet" as None, and the fixture's document read.
import httpx  # noqa: E402


def _client(routes: dict):
    """An httpx client answering each URL substring in `routes` with
    (status, body); anything else is a 404."""
    def handler(request):
        for needle, (status, body) in routes.items():
            if needle in str(request.url):
                content = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body))
                return httpx.Response(status, content=content)
        return httpx.Response(404, content=b"")
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _Page:
    def __init__(self, text="", words=None):
        self._text, self._words = text, words or []

    def extract_text(self):
        return self._text

    def extract_words(self):
        return self._words


class _Pdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_pdf(monkeypatch, module, pages):
    monkeypatch.setattr(module.pdfplumber, "open", lambda *_a, **_k: _Pdf(pages))


class TestFetchPaths:
    async def test_wyoming(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_wy as wy

        rows = _fixture("wy_2024_general_rows.json")["rows"]
        monkeypatch.setattr(wy, "_find_summary_sheet_rows", lambda _b: rows)
        async with _client({"2024_Wyoming_General_Results.zip": (200, b"zip")}) as c:
            count = await wy.fetch_certified_general(c, date(2024, 11, 5), "WY", {})
            assert [x.office for x in count.contests] == ["S", "H"] and count.official
        async with _client({}) as c:  # not published yet
            assert await wy.fetch_certified_general(c, date(2024, 11, 5), "WY", {}) is None

    async def test_kansas(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_ks as ks

        listing = ('<a href="24elec/2024-General-Election-Official-Vote-Totals.pdf" '
                   'title="Click to open the 2024 General Election Official results in a new window">')
        _fake_pdf(monkeypatch, ks, [_Page("\n".join(_fixture("ks_2024_general_lines.json")["lines"]))])
        async with _client({"election-results.html": (200, listing), ".pdf": (200, b"%PDF")}) as c:
            count = await ks.fetch_certified_general(c, date(2024, 11, 5), "KS", {})
            assert len(count.contests) == 4
            assert await ks.fetch_certified_general(c, date(2026, 11, 3), "KS", {}) is None

    async def test_massachusetts(self):
        from app.pipeline.fetch import state_candidates_ma as ma

        pages = _fixture("ma_2024_general_pages.json")
        routes = {
            "office_id:6": (200, '<a href="/elections/view/1/">'),
            "office_id:5": (200, '<a href="/elections/view/2/">'),
            "/elections/view/1/": (200, pages["senate"]),
            "/elections/view/2/": (200, pages["house_8"]),
        }
        async with _client(routes) as c:
            count = await ma.fetch_certified_general(c, date(2024, 11, 5), "MA", {})
            assert sorted((x.office, x.district) for x in count.contests) == [("H", 8), ("S", None)]
        async with _client({"office_id": (200, "no races yet")}) as c:
            assert await ma.fetch_certified_general(c, date(2026, 11, 3), "MA", {}) is None

    async def test_new_jersey(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_nj as nj

        words = _fixture("nj_2024_general_house_pages.json")["pages"]
        _fake_pdf(monkeypatch, nj, [_Page("Official List", w) for w in words])
        page = 'href="assets/pdf/election-results/2024/2024-official-general-results-us-house.pdf"'
        async with _client({"election-information-2024": (200, page), ".pdf": (200, b"%PDF")}) as c:
            count = await nj.fetch_certified_general(c, date(2024, 11, 5), "NJ", {})
            assert {x.district for x in count.contests} >= {1, 12}
        # 2026's page links the lists before they exist: a 404 is "not yet".
        page26 = page.replace("2024", "2026")
        async with _client({"election-information-2026": (200, page26)}) as c:
            assert await nj.fetch_certified_general(c, date(2026, 11, 3), "NJ", {}) is None

    async def test_oregon(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_or as orr

        fixture = _fixture("or_2024_general_pages.json")["pages"]
        _fake_pdf(monkeypatch, orr, [_Page(p["text"], [w for line in p["lines"] for w in line]) for p in fixture])
        row = {"value": [{"Election_x0020_Date": "2024-11-05T06:00:00Z",
                          "Results": '<a href="https&#58;//x/RecordViewer.aspx?uri=13735458">Official Results of November General</a>'}]}
        async with _client({"_api/web/lists": (200, row), "DocumentStream": (200, b"%PDF")}) as c:
            count = await orr.fetch_certified_general(c, date(2024, 11, 5), "OR", {})
            assert len(count.contests) == 6
            assert await orr.fetch_certified_general(c, date(2026, 11, 3), "OR", {}) is None

    async def test_california(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_ca_sov as ca

        sov = _fixture("ca_2024_general_sov.json")
        books = {"25-us-rep-congress.xlsx": sov["house"], "19-uss-ft.xlsx": sov["senate_full_term"]}
        _fake_pdf(monkeypatch, ca, [_Page(sov["certificate"])])
        monkeypatch.setattr(ca, "xlsx_sheet_rows", lambda content: books[content.decode()])
        page = ('<a href="https://e/sov/2024-general/sov/15-sov-certificate.pdf">'
                '<a href="https://e/sov/2024-general/sov/25-us-rep-congress.xlsx">'
                '<a href="https://e/sov/2024-general/sov/19-uss-ft.xlsx">')
        routes = {"general-election-nov-5-2024": (200, page), "certificate.pdf": (200, b"%PDF"),
                  "25-us-rep-congress.xlsx": (200, b"25-us-rep-congress.xlsx"),
                  "19-uss-ft.xlsx": (200, b"19-uss-ft.xlsx")}
        async with _client(routes) as c:
            count = await ca.fetch_certified_general(c, date(2024, 11, 5), "CA", {})
            assert count.certified_on == date(2024, 12, 13) and len(count.contests) == 3
            assert await ca.fetch_certified_general(c, date(2026, 11, 3), "CA", {}) is None  # no page yet

    async def test_vermont(self):
        from app.pipeline.fetch import state_candidates_vt as vt

        guid = "g-1"
        routes = {
            "elections/elections.json": (200, [{"isStateWideElection": True, "electionTypeCode": "G",
                                               "electionYear": 2026, "electionGuid": guid}]),
            f"elections/{guid}.json": (200, {
                "electionDetails": {"electionDate": "2026-11-03T00:00:00", "electionTypeCode": "G",
                                    "isOfficial": False},
                "federal": {"isEnable": True, "path": f"elections\\{guid}-f-20261103211104.json"},
                "townsReporting": "141/247"}),
            f"{guid}-f-": (200, _fixture("vt_2026_general_report.json")["report"]),
        }
        async with _client(routes) as c:
            count = await vt.fetch_general_results(c, DAY, "VT", {})
            (house,) = count.contests
            assert (house.reporting_units, house.total_units) == (141, 247)
            assert count.source_version == f"{guid}:20261103211104" and not count.units_complete
            assert await vt.fetch_general_results(c, date(2024, 11, 5), "VT", {}) is None


class TestNorthCarolinaReader:
    def test_reads_the_2024_certified_results_and_dates_them_by_the_canvass(self):
        from app.pipeline.fetch.state_candidates_nc_certified import certified_link, general_contests

        fixture = _fixture("nc_2024_general_page.json")
        contests = {c.district: c for c in general_contests([fixture["words"]])}
        assert set(contests) >= set(range(1, 11))
        first = contests[1]
        assert ("Don Davis", "D", 186_341) in first.candidates
        assert first.total_votes == 186_341 + 9_949 + 180_034
        url, on = certified_link(fixture["link"], date(2024, 11, 5))
        assert on == date(2024, 11, 26) and url.endswith(".pdf")
        assert certified_link(fixture["link"], date(2026, 11, 3)) is None

    async def test_fetch_path(self, monkeypatch):
        from app.pipeline.fetch import state_candidates_nc_certified as nc

        fixture = _fixture("nc_2024_general_page.json")
        _fake_pdf(monkeypatch, nc, [_Page("", []), _Page("", fixture["words"])])
        async with _client({"historical-election-results-data": (200, fixture["link"]),
                            "State_Board_Meeting_Docs": (200, b"%PDF")}) as c:
            count = await nc.fetch_certified_general(c, date(2024, 11, 5), "NC", {})
            assert count.certified_on == date(2024, 11, 26) and count.contests
            assert await nc.fetch_certified_general(c, date(2026, 11, 3), "NC", {}) is None


class TestCertificationDeadlines:
    def test_every_entry_quotes_and_cites_its_rule(self):
        from app import certification_deadlines as cd

        for state in cd.states():
            entry = cd.rule_for(state)
            assert entry["statute"] and entry["quote"], state
            assert cd.deadline(state, DAY) > DAY

    def test_deadline_rules(self):
        from app.certification_deadlines import deadline

        assert deadline("CO", DAY) == date(2026, 11, 30)  # 27 days
        assert deadline("NE", DAY) == date(2026, 11, 30)  # 4th Monday after
        assert deadline("NM", DAY) == date(2026, 11, 24)  # 3rd Tuesday after (2024's canvass: Nov 26)
        assert deadline("UT", DAY) == date(2026, 11, 23)  # 4th Monday of November
        assert deadline("VA", DAY) == date(2026, 12, 7)   # 1st Monday of December
        assert deadline("CT", DAY) == date(2026, 11, 25)  # last Wednesday of November
        assert deadline("RI", DAY) is None                # no fixed deadline: not read this way

    def _count(self, db, last_change, reporting=650, total=650):
        from app.models import RaceResult

        race = Race(id="2026-HOUSE-CO-8", cycle_year=2026, office="H", state="CO", district=8)
        db.add(race)
        db.add(RaceResult(
            race_id=race.id, election_date=DAY.isoformat(), source_name="Colorado Secretary of State",
            source_url="https://results.example/co",
            tallies=json.dumps([{"name": "Dana Smith", "party": "DEM", "votes": 160_000, "candidateId": None},
                                {"name": "Ray Jones", "party": "REP", "votes": 140_000, "candidateId": None}]),
            votes_counted=300_000, reporting_units=reporting, total_units=total, unit_label="counties",
            official=False, first_reported_at=datetime(2026, 11, 4), last_change_at=last_change,
        ))
        db.flush()
        return race

    def test_a_count_unchanged_past_the_deadline_is_taken_as_certified(self, db_session):
        race = self._count(db_session, datetime(2026, 11, 20, 12))
        # Not before the deadline (Nov 30) and its day of grace.
        assert cr.apply_deadlines(db_session, DAY, date(2026, 12, 1))["stored"] == 0
        assert cr.apply_deadlines(db_session, DAY, date(2026, 12, 2))["stored"] == 1
        row = db_session.get(RaceCertification, race.id)
        view = cr.certification_json(row, date(2026, 12, 2))
        assert view["basis"] == cr.DEADLINE and view["certifiedOn"] == "2026-11-30"
        # Colorado: any candidate may ask for a recount within 28 days of the
        # election, long past; the margin is outside its automatic recount.
        assert view["status"] == rr.CERTIFIED and view["winner"]["name"] == "Dana Smith"

    def test_a_count_still_moving_or_short_of_its_units_is_not(self, db_session):
        race = self._count(db_session, datetime(2026, 12, 1, 12))  # moved after the deadline
        assert cr.apply_deadlines(db_session, DAY, date(2026, 12, 5))["stored"] == 0
        assert db_session.get(RaceCertification, race.id) is None

    def test_short_of_units(self, db_session):
        self._count(db_session, datetime(2026, 11, 20), reporting=640)
        assert cr.apply_deadlines(db_session, DAY, date(2026, 12, 5))["stored"] == 0

    def test_a_count_that_moves_later_is_withdrawn_and_alerted(self, db_session, monkeypatch):
        from app.models import RaceResult

        sent = []
        monkeypatch.setattr(cr, "send_ops_alert", lambda *a, **k: sent.append(k["condition"]))
        race = self._count(db_session, datetime(2026, 11, 20))
        cr.apply_deadlines(db_session, DAY, date(2026, 12, 2))
        db_session.get(RaceResult, race.id).last_change_at = datetime(2026, 12, 10)  # a recount moved it
        out = cr.apply_deadlines(db_session, DAY, date(2026, 12, 11))
        assert out["withdrawn"] == [race.id] and sent == ["certified-deadline-moved"]
        assert db_session.get(RaceCertification, race.id) is None

    def test_the_state_s_own_certification_is_never_replaced(self, db_session):
        race = self._count(db_session, datetime(2026, 11, 20))
        db_session.add(RaceCertification(race_id=race.id, cycle=2026, election_date=DAY.isoformat(), state="CO",
                                         office="H", district=8, basis="certified", certified_on="2026-11-25",
                                         source_name="CO", tallies="[]", certified_on_stated=True))
        db_session.flush()
        assert cr.apply_deadlines(db_session, DAY, date(2026, 12, 5))["stored"] == 0
        assert db_session.get(RaceCertification, race.id).basis == "certified"


class TestSettled:
    """certified.refresh_settled: what election_phase ends the results
    window from."""

    def _result(self, db, race, projected):
        from app.models import RaceResult

        db.add(RaceResult(race_id=race.id, election_date=DAY.isoformat(), source_name="Maryland State Board",
                          tallies="[]", votes_counted=100_000, projected=projected,
                          first_reported_at=datetime(2026, 11, 4), last_change_at=datetime(2026, 11, 4)))
        db.flush()
        return db.get(RaceResult, race.id)

    def test_a_projection_settles_and_a_withdrawal_reopens(self, db_session, freeze_utcnow):
        result = self._result(db_session, _race(db_session), projected=True)
        freeze_utcnow(datetime(2026, 11, 4, 6))
        assert cr.refresh_settled(db_session, DAY, date(2026, 11, 4)) == 0
        assert result.settled_at == datetime(2026, 11, 4, 6)
        freeze_utcnow(datetime(2026, 11, 4, 8))
        cr.refresh_settled(db_session, DAY, date(2026, 11, 4))
        assert result.settled_at == datetime(2026, 11, 4, 6)  # first settled, kept
        result.projected = False
        assert cr.refresh_settled(db_session, DAY, date(2026, 11, 4)) == 1
        assert result.settled_at is None

    def test_a_certified_winner_settles_without_a_projection(self, db_session):
        race = _race(db_session)
        result = self._result(db_session, race, projected=False)
        cr.apply_certified(db_session, "MD", DAY, _certified(date(2026, 12, 4)), date(2026, 12, 4))
        # Maryland's recount can still be asked for until Dec 7.
        assert cr.refresh_settled(db_session, DAY, date(2026, 12, 4)) == 1
        assert cr.refresh_settled(db_session, DAY, date(2026, 12, 8)) == 0
        assert result.settled_at is not None

    def test_a_certified_count_inside_a_recount_reopens_a_projection(self, db_session):
        race = _race(db_session)
        result = self._result(db_session, race, projected=True)
        cr.apply_certified(db_session, "MD", DAY, _certified(date(2026, 12, 4), d=50_010, r=50_000),
                           date(2026, 12, 4))
        # Inside the margin Maryland lets a candidate ask a recount at.
        assert cr.outcome_for(db_session.get(RaceCertification, race.id), date(2026, 12, 4)).status \
            == rr.RECOUNT_POSSIBLE
        assert cr.refresh_settled(db_session, DAY, date(2026, 12, 4)) == 1
        assert result.settled_at is None


class TestElectedUnopposed:
    FROST = {"office": "H", "district": 10, "display_name": "Maxwell Alejandro Frost", "last_name": "Frost",
             "party": "D", "party_label": "DEM", "unopposed": True}

    def _seat(self, db, *others):
        race = Race(id="2026-HOUSE-FL-10", cycle_year=2026, office="H", state="FL", district=10)
        db.add(race)
        db.add(Candidate(id="H6FL10001", race_id=race.id, name="FROST, MAXWELL ALEJANDRO", party="DEM",
                         confirmed_general=True))
        for i, name in enumerate(others):
            db.add(Candidate(id=f"H6FL1000{i + 2}", race_id=race.id, name=name, party="REP", confirmed_general=True))
        db.flush()
        return race

    def test_the_sole_candidate_the_list_marks_unopposed_is_the_winner(self, db_session):
        race = self._seat(db_session)
        stored, events = cr.record_unopposed(db_session, "FL", DAY, [self.FROST], date(2026, 11, 10))
        assert stored == 1 and len(events) == 1
        view = cr.certification_json(db_session.get(RaceCertification, race.id), date(2026, 11, 10))
        assert view["status"] == cr.UNOPPOSED and view["basis"] == cr.UNOPPOSED
        assert view["winner"] == {"name": "Maxwell Alejandro Frost", "party": "DEM", "candidateId": "H6FL10001"}
        assert view["statute"] == "Fla. Stat. 101.151(7)"  # the source entry's
        assert view["votesCounted"] is None and view["certifiedOn"] is None
        # Said once, and stored again unchanged on the next day's read.
        assert cr.record_unopposed(db_session, "FL", DAY, [self.FROST], date(2026, 11, 11)) == (1, [])

    def test_a_race_on_file_with_an_opponent_is_not_taken_from_the_list(self, db_session):
        race = self._seat(db_session, "DOE, JANE")
        assert cr.record_unopposed(db_session, "FL", DAY, [self.FROST], date(2026, 11, 10)) == (0, [])
        assert db_session.get(RaceCertification, race.id) is None

    def test_a_certified_count_is_never_replaced(self, db_session):
        race = self._seat(db_session)
        cr._store(db_session, race, DAY, "certified", "2026-11-17", "Florida", None,
                  [{"name": "Maxwell Frost", "party": "DEM", "votes": 1}], 1)
        cr.record_unopposed(db_session, "FL", DAY, [self.FROST], date(2026, 11, 20))
        assert db_session.get(RaceCertification, race.id).basis == "certified"

    def test_the_sync_reads_the_list_and_alerts_when_it_cannot(self, db_session, monkeypatch):
        race = self._seat(db_session)
        alerts = []

        async def listed(client, state, day):
            return [self.FROST]

        monkeypatch.setattr(cr, "certified_states", lambda: set())
        monkeypatch.setattr(cr, "unopposed_states", lambda: {"FL"})
        monkeypatch.setattr(cr, "fetch_elected_unopposed", listed)
        monkeypatch.setattr(cr, "send_ops_alert", lambda *a, **k: alerts.append(a))
        summary = asyncio.run(cr.sync_certified(db_session, None, DAY, date(2026, 11, 10)))
        assert summary["FL:unopposed"] == {"status": "ok", "races": 1, "events": 1}
        assert db_session.get(RaceCertification, race.id).basis == cr.UNOPPOSED

        async def down(client, state, day):
            from app.pipeline.fetch.election_results import FeedUnreadable

            raise FeedUnreadable("FL candidate list could not be read")

        monkeypatch.setattr(cr, "fetch_elected_unopposed", down)
        summary = asyncio.run(cr.sync_certified(db_session, None, DAY, date(2026, 11, 11)))
        assert summary["FL:unopposed"]["status"] == "unavailable" and alerts
        # The winner already on file stands.
        assert db_session.get(RaceCertification, race.id).basis == cr.UNOPPOSED

    def test_florida_is_read_for_unopposed_winners(self):
        from app.pipeline.fetch.election_results import unopposed_states

        assert "FL" in unopposed_states()
