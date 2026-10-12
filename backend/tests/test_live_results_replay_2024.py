"""Election night, replayed from real feeds: each state's own 2024 count as
the Internet Archive captured it during the night, read through the real
readers and the real sync (sync_live_results) on a moving clock.

Fixtures (tests/fixtures/live_results/, each with its own _source; candidate
names replaced, every figure as published):

  co_general_2024_clarity_series.json   Colorado (Clarity), CO-3, CO-5 and
      CO-8 at seven versions from the staged zero file to the final count.
      Clarity's county roll-up counts counties COMPLETELY reported: CO-8 read
      0 of 3 all night beside 230,000 votes, and its seat changed party only
      in the final count.
  nm_general_2024_totalvote_series.json  New Mexico (TotalVote), NM-2 from
      the day before to the final count; the challenger's party led at 0 of
      656 precincts "Fully" reported.
  ut_general_2024_enhanced_voting.json   Utah (Enhanced Voting), two days in
      (every county reporting, two thirds of the vote counted) and final.
"""

import json
from datetime import date, datetime
from email.utils import format_datetime
from datetime import timezone
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.live_results import bluesky as eb
from app.live_results import sync as er
from app.live_results import signals
from app.models import ActionIssue, BroadcastPost, ElectionResultEvent, Race, RaceResult, Representative
from app.pipeline import rate_limiter
from app.pipeline.fetch import state_candidates_clarity as clarity

FIXTURES = Path(__file__).parent / "fixtures" / "live_results"
DAY = date(2024, 11, 5)


def _json(name):
    return json.loads((FIXTURES / name).read_text())


class Feed:
    """The vendor's host, answering from the fixture for the current step."""

    def __init__(self):
        self.routes: dict[str, tuple[int, bytes, dict]] = {}
        self.requests: list[str] = []

    def serve(self, url, body, headers=None, status=200):
        self.routes[url] = (status, body if isinstance(body, bytes) else body.encode(), headers or {})

    def handler(self, request):
        url = str(request.url)
        self.requests.append(url)
        status, body, headers = self.routes.get(url, (404, b"", {}))
        return httpx.Response(status, content=body, headers=headers, request=request)


@pytest.fixture()
def feed(monkeypatch):
    async def no_wait(self):
        return None

    monkeypatch.setattr(rate_limiter.RateLimiter, "acquire", no_wait)
    # The 2024 count's seats were held by the 118th Congress's members.
    monkeypatch.setattr(settings, "CURRENT_CONGRESS", 118)
    clarity._general_eids.clear()
    clarity._general_eid_misses.clear()
    f = Feed()
    yield f
    clarity._general_eids.clear()
    clarity._general_eid_misses.clear()


def _client(feed):
    return httpx.AsyncClient(transport=httpx.MockTransport(feed.handler))


def _house(db, state, district, held):
    db.add(Race(id=f"2024-HOUSE-{state}-{district}", cycle_year=2024, office="H", state=state, district=district))
    db.add(Representative(id=f"X{state}{district:02d}", name=f"Member {state}-{district}", state=state,
                          district=district, party=held, is_current=True))


async def _sync(db, feed, monkeypatch, state, freeze_utcnow, at):
    freeze_utcnow(at)
    monkeypatch.setattr(er, "live_results_states", lambda: {state})
    async with _client(feed) as client:
        return (await er.sync_live_results(db, client, DAY))[state]


def _events(db, race_id=None):
    q = db.query(ElectionResultEvent)
    if race_id:
        q = q.filter(ElectionResultEvent.race_id == race_id)
    return [(e.kind, json.loads(e.detail)) for e in q.order_by(ElectionResultEvent.id)]


def _every_text(db) -> list[str]:
    texts = [p.text for p in db.query(BroadcastPost)]
    for issue in db.query(ActionIssue):
        texts += [issue.title, issue.summary, *json.loads(issue.facts or "[]")]
    return texts


class TestColoradoClarity2024:
    SERIES = _json("co_general_2024_clarity_series.json")
    BASE = "https://results.enr.clarityelections.com/CO"

    def _serve(self, feed, version):
        step = next(v for v in self.SERIES["versions"] if v["version"] == version)
        captured = datetime.strptime(step["captured"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        feed.serve(f"{self.BASE}/elections.json", json.dumps([
            {"EID": "122598", "ElectionName": "2024 General", "Date": "11/5/2024 12:00:00 AM"},
            {"EID": "121727", "ElectionName": "2024 Primary", "Date": "6/25/2024 12:00:00 AM"},
        ]))
        feed.serve(f"{self.BASE}/122598/current_ver.txt", str(version))
        feed.serve(f"{self.BASE}/122598/{version}/json/sum.json", json.dumps({"Contests": step["Contests"]}),
                   {"Last-Modified": format_datetime(captured, usegmt=True)})
        feed.serve(f"{self.BASE}/122598/{version}/json/en/electionsettings.json",
                   json.dumps(self.SERIES["electionsettings"]))

    def _seed(self, db):
        _house(db, "CO", 3, "R")
        _house(db, "CO", 5, "R")
        _house(db, "CO", 8, "D")
        db.commit()

    async def test_the_night_as_it_happened(self, db_session, feed, monkeypatch, freeze_utcnow):
        db = db_session
        self._seed(db)
        sync = lambda at: _sync(db, feed, monkeypatch, "CO", freeze_utcnow, at)  # noqa: E731

        # A minute before Colorado's polls close (7 PM MST = 02:00Z): the
        # staged file is up, and nothing is read.
        self._serve(feed, 350435)
        assert (await sync(datetime(2024, 11, 6, 1, 59)))["status"] == "polls_open"
        assert feed.requests == [] and db.query(RaceResult).count() == 0

        # At close, the zero file: every race stored, nothing said.
        out = await sync(datetime(2024, 11, 6, 2, 0, 30))
        assert out["status"] == "ok" and out["races"] == 3 and out["events"] == 0
        co8 = db.get(RaceResult, "2024-HOUSE-CO-8")
        assert co8.units_complete is True and co8.unit_label == "counties"
        assert signals.reporting_line(er.event_detail(co8)) == "0 of 3 counties fully reported (0%)"
        assert signals.reporting_line(er.event_detail(db.get(RaceResult, "2024-HOUSE-CO-5"))) == \
            "0 of 1 county fully reported (0%)"

        self._serve(feed, 351262)
        await sync(datetime(2024, 11, 6, 2, 8, 19))
        assert [k for k, _ in _events(db, "2024-HOUSE-CO-3")] == [er.FIRST_RETURNS]
        assert _events(db, "2024-HOUSE-CO-8") == []  # no votes in CO-8 yet

        # CO-8's first votes put the challenger's party ahead at 0 of 3
        # counties complete: a lead, never a change of party.
        self._serve(feed, 351362)
        await sync(datetime(2024, 11, 6, 2, 18, 30))
        [(kind, d)] = _events(db, "2024-HOUSE-CO-8")
        assert kind == er.FIRST_RETURNS and d["leader"]["party"] == "REP" and d["heldBy"] == "DEM"
        assert db.get(RaceResult, "2024-HOUSE-CO-8").flip_announced is False

        # CO-3's lead goes to the challenger's party, then back.
        self._serve(feed, 351526)
        await sync(datetime(2024, 11, 6, 2, 32, 9))
        self._serve(feed, 351576)
        await sync(datetime(2024, 11, 6, 2, 39, 29))
        co3 = _events(db, "2024-HOUSE-CO-3")
        assert [k for k, _ in co3] == [er.FIRST_RETURNS, er.LEAD_CHANGE, er.LEAD_CHANGE]
        assert co3[1][1]["leader"]["party"] == "DEM"
        assert signals.reporting_line(co3[1][1]) == "5 of 27 counties fully reported (19%)"
        assert not any(k == er.FLIP for k, _ in _events(db))

        # A cache serving an older version is refused, and stores nothing.
        self._serve(feed, 352921)
        await sync(datetime(2024, 11, 6, 5, 47, 52))
        before = db.get(RaceResult, "2024-HOUSE-CO-3").votes_counted
        self._serve(feed, 351526)
        out = await sync(datetime(2024, 11, 6, 5, 52))
        assert out["status"] == "stale" and "went back" in out["reason"]
        # Re-served with a fresh Last-Modified, its version alone gives it away.
        sum_url = f"{self.BASE}/122598/351526/json/sum.json"
        feed.routes[sum_url] = (*feed.routes[sum_url][:2], {"Last-Modified": "Wed, 06 Nov 2024 05:51:00 GMT"})
        out = await sync(datetime(2024, 11, 6, 5, 53))
        assert out["status"] == "stale" and "version went back" in out["reason"]
        assert db.get(RaceResult, "2024-HOUSE-CO-3").votes_counted == before

        # The final count: every county complete, and CO-8's lead has gone to
        # the challenger's party — the flip, announced at last.
        self._serve(feed, 367167)
        await sync(datetime(2025, 11, 26, 12))
        co8 = _events(db, "2024-HOUSE-CO-8")
        assert [k for k, _ in co8][-2:] == [er.ALL_REPORTING, er.FLIP]
        assert db.get(RaceResult, "2024-HOUSE-CO-8").flip_announced is True
        [post] = db.query(BroadcastPost).filter(BroadcastPost.subject.like("%CO-8:flip")).all()
        assert post.text.startswith("Colorado's 8th Congressional District: Candidate B-8 (R) leads in a seat "
                                    "Democrats hold.")
        assert "3 of 3 counties fully reported (100%)" in post.text and post.text.endswith("Not final.")
        [(_, co5_all)] = [e for e in _events(db, "2024-HOUSE-CO-5") if e[0] == er.ALL_REPORTING]
        race5 = db.get(Race, "2024-HOUSE-CO-5")
        assert eb.compose(er.ALL_REPORTING, race5, co5_all).startswith(
            "Colorado's 5th Congressional District: its one county has fully reported.")
        texts = _every_text(db)
        assert texts and not any("win" in t.lower() for t in texts)

    async def test_a_race_the_feed_stops_listing_is_flagged_not_shown_as_live(
            self, db_session, feed, monkeypatch, freeze_utcnow):
        from app.api import elections as api

        db = db_session
        self._seed(db)
        self._serve(feed, 351526)
        await _sync(db, feed, monkeypatch, "CO", freeze_utcnow, datetime(2024, 11, 6, 2, 32))
        # The next version renames CO-3's contest past the reader.
        step = json.loads(json.dumps(next(v for v in self.SERIES["versions"] if v["version"] == 351576)))
        for c in step["Contests"]:
            if c["C"].endswith("District 3"):
                c["C"] = "Representative 3"
        feed.serve(f"{self.BASE}/122598/current_ver.txt", "351576")
        feed.serve(f"{self.BASE}/122598/351576/json/sum.json", json.dumps({"Contests": step["Contests"]}),
                   {"Last-Modified": "Wed, 06 Nov 2024 02:39:29 GMT"})
        feed.serve(f"{self.BASE}/122598/351576/json/en/electionsettings.json",
                   json.dumps(self.SERIES["electionsettings"]))
        alerts = []
        monkeypatch.setattr(er, "send_ops_alert", lambda subject, body, **kw: alerts.append((subject, kw)))
        out = await _sync(db, feed, monkeypatch, "CO", freeze_utcnow, datetime(2024, 11, 6, 2, 45))
        assert out["status"] == "ok" and out["dropped"] == ["2024-HOUSE-CO-3"]
        assert [s for s, _ in alerts] == ["Live results: CO feed no longer lists a race"]
        rows = {r.race_id: r for r in db.query(RaceResult)}
        last_ok = datetime(2024, 11, 6, 2, 45)
        assert api._result_json(rows["2024-HOUSE-CO-3"], db.get(Race, "2024-HOUSE-CO-3"), last_ok)["inLatestRead"] \
            is False
        assert api._result_json(rows["2024-HOUSE-CO-8"], db.get(Race, "2024-HOUSE-CO-8"), last_ok)["inLatestRead"] \
            is True


class TestNewMexicoTotalVote2024:
    SERIES = _json("nm_general_2024_totalvote_series.json")
    URL = "https://electionresults.sos.nm.gov/resultsSW.aspx?type=FED&map=CTY"

    def _serve(self, feed, captured):
        s = next(x for x in self.SERIES["steps"] if x["captured"] == captured)
        page = (f'<html><body><input type="hidden" id="hidElectionType" value="{s["hidElectionType"]}" />'
                f'<input type="hidden" id="hidElectionDate" value="{s["hidElectionDate"]}" />'
                f'<span>Results last updated:</span><span>{s["lastUpdated"]}</span>{s["contest"]}</body></html>')
        feed.serve(self.URL, page)

    async def test_the_night_as_it_happened(self, db_session, feed, monkeypatch, freeze_utcnow):
        db = db_session
        _house(db, "NM", 2, "D")
        db.commit()
        sync = lambda at: _sync(db, feed, monkeypatch, "NM", freeze_utcnow, at)  # noqa: E731

        self._serve(feed, "20241105024638")  # the staged page, the evening before
        assert (await sync(datetime(2024, 11, 5, 2, 46, 38)))["status"] == "polls_open"
        assert (await sync(datetime(2024, 11, 6, 1, 59, 59)))["status"] == "polls_open"
        assert feed.requests == []

        self._serve(feed, "20241106020417")
        assert (await sync(datetime(2024, 11, 6, 2, 4, 17)))["status"] == "ok"
        assert _events(db) == []

        # First votes, the challenger's party ahead, not one precinct fully in.
        self._serve(feed, "20241106021250")
        await sync(datetime(2024, 11, 6, 2, 12, 50))
        [(kind, d)] = _events(db)
        assert kind == er.FIRST_RETURNS and d["leader"]["party"] == "REP"
        assert signals.reporting_line(d) == "0 of 656 precincts fully reported (0%)"

        self._serve(feed, "20241106022011")
        await sync(datetime(2024, 11, 6, 2, 20, 11))
        self._serve(feed, "20241106023031")
        await sync(datetime(2024, 11, 6, 2, 30, 31))
        kinds = [k for k, _ in _events(db)]
        assert kinds == [er.FIRST_RETURNS, er.LEAD_CHANGE, er.LEAD_CHANGE]
        result = db.get(RaceResult, "2024-HOUSE-NM-2")
        assert er.challenger_leads(result) and not er.flip_qualifies(result)
        assert not result.flip_announced and db.query(ActionIssue).count() == 0

        # The page stamped earlier than the one stored: refused.
        self._serve(feed, "20241106022011")
        out = await sync(datetime(2024, 11, 6, 2, 35))
        assert out["status"] == "stale" and "went back" in out["reason"]

        self._serve(feed, "20241106070109")
        await sync(datetime(2024, 11, 6, 7, 1, 9))
        self._serve(feed, "20241106073309")
        await sync(datetime(2024, 11, 6, 7, 33, 9))
        self._serve(feed, "20241127060054")
        await sync(datetime(2024, 11, 27, 6, 0, 54))
        assert [k for k, _ in _events(db)][-1] == er.ALL_REPORTING
        assert signals.all_in_line(_events(db)[-1][1]) == "all 656 precincts have fully reported"
        assert not any(k == er.FLIP for k, _ in _events(db))


    async def test_the_vendors_test_run_on_the_real_page_is_refused(self, db_session, feed, monkeypatch, freeze_utcnow):
        """Four weeks out the real general page carried test votes under the
        general's own date, marked only by "Testing in Progress"."""
        s = self.SERIES["testing_capture"]
        page = (f'<html><body><input type="hidden" id="hidElectionType" value="{s["hidElectionType"]}" />'
                f'<input type="hidden" id="hidElectionDate" value="{s["hidElectionDate"]}" />'
                f'<span>Results last updated:</span><span>{s["lastUpdated"]}</span>{s["siteMessage"]}'
                f'{s["contest"]}</body></html>')
        _house(db_session, "NM", 2, "D")
        db_session.commit()
        feed.serve(self.URL, page)
        out = await _sync(db_session, feed, monkeypatch, "NM", freeze_utcnow, datetime(2024, 11, 6, 2, 5))
        assert out["status"] == "untrusted" and "Testing in Progress" in out["reason"]
        assert db_session.query(RaceResult).count() == 0
        # Without the panel the same page is read: the panel is the only mark.
        feed.serve(self.URL, page.replace(s["siteMessage"], ""))
        out = await _sync(db_session, feed, monkeypatch, "NM", freeze_utcnow, datetime(2024, 11, 6, 2, 10))
        assert out["status"] == "ok"


class TestUtahEnhancedVoting2024:
    FIX = _json("ut_general_2024_enhanced_voting.json")
    INDEX = "https://electionresults.utah.gov/results/public/api/jurisdictions/Utah"
    DATA = "https://electionresults.utah.gov/results/public/api/elections/Utah/general11052024/data"

    def _serve(self, feed, which):
        feed.serve(self.INDEX, json.dumps({"elections": [
            {"publicElectionId": "general11052024", "electionDate": "2024-11-05T00:00:00",
             "name": [{"languageId": "en", "text": "2024 General Election"}]},
            {"publicElectionId": "primary06252024", "electionDate": "2024-06-25T00:00:00",
             "name": [{"languageId": "en", "text": "2024 Primary Election"}]},
        ]}))
        feed.serve(self.DATA, json.dumps(self.FIX[which]))

    async def test_every_county_reporting_is_not_every_vote_counted(self, db_session, feed, monkeypatch, freeze_utcnow):
        db = db_session
        _house(db, "UT", 1, "R")
        db.add(Race(id="2024-SEN-UT", cycle_year=2024, office="S", state="UT"))
        db.commit()
        self._serve(feed, "two_days_in")
        out = await _sync(db, feed, monkeypatch, "UT", freeze_utcnow, datetime(2024, 11, 7, 1, 30))
        assert out["status"] == "ok"
        house = db.get(RaceResult, "2024-HOUSE-UT-1")
        assert house.unit_label == "counties" and house.units_complete is False
        # 8 of 8 counties have results in, with 255,235 of 365,895 votes.
        assert signals.reporting_line(er.event_detail(house)) == "Results from 8 of 8 counties (100%)"
        [(_, all_in)] = [e for e in _events(db, "2024-SEN-UT") if e[0] == er.ALL_REPORTING]
        assert eb.compose(er.ALL_REPORTING, db.get(Race, "2024-SEN-UT"), all_in).startswith(
            "Utah's U.S. Senate: results are in from all 29 counties. ")

        self._serve(feed, "final")
        await _sync(db, feed, monkeypatch, "UT", freeze_utcnow, datetime(2024, 11, 23, 12))
        assert db.get(RaceResult, "2024-HOUSE-UT-1").votes_counted == 365895
        senate = [k for k, _ in _events(db, "2024-SEN-UT")]
        assert senate[-1] == er.OFFICIAL
        posts = [p.text for p in db.query(BroadcastPost)]
        assert any(t.startswith("Utah's U.S. Senate: the state lists its count as official.") for t in posts)
        assert not any("win" in t.lower() for t in _every_text(db))


def test_a_share_short_of_every_unit_never_reads_as_all_of_them():
    """Montana's 2024 Senate count dipped to 724 of 727 precincts after
    reading all 727; plain rounding printed that as 100%."""
    line = signals.reporting_line
    assert line({"reportingUnits": 724, "totalUnits": 727, "unitLabel": "precincts", "unitsComplete": True}) == \
        "724 of 727 precincts fully reported (99%)"
    assert line({"reportingUnits": 1, "totalUnits": 656, "unitLabel": "precincts", "unitsComplete": True}) == \
        "1 of 656 precincts fully reported (<1%)"
    assert line({"reportingUnits": 727, "totalUnits": 727, "unitLabel": "precincts", "unitsComplete": True}) == \
        "727 of 727 precincts fully reported (100%)"
