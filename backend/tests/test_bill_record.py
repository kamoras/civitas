"""A bill's public record for its detail page (any bill), and one roll
call with every member's position."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import RollCall, RollCallPosition, Senator
from app.services import bill_record as br

# Congress.gov's answer for S. 4668 (119th), trimmed to what is shaped.
BILL = {"title": "Protect College Sports Act of 2026", "introducedDate": "2026-06-02", "originChamber": "Senate",
        "policyArea": {"name": "Sports and Recreation"},
        "latestAction": {"actionDate": "2026-09-24", "text": "The committee substitute tabled by Voice Vote."},
        "sponsors": [{"bioguideId": "C001098", "fullName": "Sen. Cruz, Ted [R-TX]", "party": "R", "state": "TX"}],
        "cboCostEstimates": [{"title": "S. 4668, Protect College Sports Act of 2026", "pubDate": "2026-07-31T18:43:00Z",
                              "description": "As reported by the Senate Committee on\\nCommerce", "url": "https://www.cbo.gov/publication/62630"}]}
SUMMARIES = [
    {"actionDate": "2026-06-02", "actionDesc": "Introduced in Senate", "updateDate": "2026-06-10",
     "text": "<p><strong>Protect College Sports Act of 2026</strong></p><p>Old.</p>"},
    {"actionDate": "2026-06-24", "actionDesc": "Reported to Senate", "updateDate": "2026-07-01",
     "text": "<p><strong>Protect College Sports Act of 2026</strong></p><p>This bill establishes requirements for name, image, or likeness (NIL) agreements &amp; more.</p>"},
]
ACTIONS = [{"actionDate": "2026-09-24", "type": "Floor",
            "text": "Cloture on the measure, as amended, invoked in Senate by Yea-Nay Vote. 74 - 25. Record Vote Number: 243.",
            "recordedVotes": [{"chamber": "Senate", "rollNumber": 243, "sessionNumber": 2}]}]
COSPONSORS = [{"bioguideId": "C000127", "fullName": "Sen. Cantwell, Maria [D-WA]", "party": "D", "state": "WA",
               "isOriginalCosponsor": True, "sponsorshipDate": "2026-06-02"}]
TEXT = [{"date": "2026-06-24T04:00:00Z", "type": "Reported to Senate",
         "formats": [{"type": "PDF", "url": "https://www.congress.gov/119/bills/s4668/BILLS-119s4668rs.pdf"}]}]


def _answers(fail: set[str] = frozenset(), missing: bool = False):
    calls = []

    async def fake(client, url):
        calls.append(url)
        if missing:
            return br.NOT_FOUND
        for part, suffix, body in (("summaries", "/summaries", {"summaries": SUMMARIES}),
                                   ("actions", "/actions", {"actions": ACTIONS}),
                                   ("cosponsors", "/cosponsors", {"cosponsors": COSPONSORS}),
                                   ("text", "/text", {"textVersions": TEXT})):
            if suffix in url:
                return None if part in fail else body
        return None if "bill" in fail else {"bill": BILL}
    return fake, calls


@pytest.fixture(autouse=True)
def _fresh_limits(throttle_store):
    """The route's per-IP window and upstream budget live in the throttle
    store every API worker shares; a store per test starts them fresh."""
    yield


@pytest.fixture
def senate(db_session):
    db_session.add(Senator(id="ted-cruz", bioguide_id="C001098", name="Ted Cruz", state="TX", party="R"))
    db_session.add(Senator(id="maria-cantwell", bioguide_id="C000127", name="Maria Cantwell", state="WA", party="D"))
    db_session.add(Senator(id="ben-ray-lujan", bioguide_id="L000570", name="Ben Ray Luján", state="NM", party="D"))
    rc = RollCall(chamber="senate", congress=119, session=2, number=243, date="2026-09-24",
                  question="On the Cloture Motion S. 4668", result="Cloture Motion Agreed to",
                  yeas=2, nays=1, not_voting=1, bill_id="S.4668")
    db_session.add(rc)
    db_session.flush()
    for last, party, state, pos in (("Cruz", "R", "TX", "Yea"), ("Cantwell", "D", "WA", "Yea"),
                                    ("Lujan", "D", "NM", "Nay"), ("Tillis", "R", "NC", "Not Voting")):
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id="S000", last_name=last, party=party,
                                        state=state, position=pos))
    db_session.commit()
    return db_session


def test_record_shapes_every_part(senate, monkeypatch):
    fake, _ = _answers()
    monkeypatch.setattr(br, "_congress_get", fake)
    raw = asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668"))
    r = br.shape_record(senate, 119, "S.4668", raw)
    assert r["billLabel"] == "S. 4668" and r["title"] == "Protect College Sports Act of 2026"
    assert r["sponsors"][0]["page"] == "/politicians/ted-cruz"
    assert r["cosponsors"][0]["page"] == "/politicians/maria-cantwell"
    # The latest summary, as plain paragraphs (no markup from another site).
    assert r["summary"]["actionDesc"] == "Reported to Senate"
    assert r["summary"]["paragraphs"][1].startswith("This bill establishes requirements for name, image, or likeness (NIL) agreements & more.")
    assert r["actions"][0]["rollCalls"] == [{"chamber": "senate", "number": 243, "session": 2}]
    assert r["votes"][0]["parties"] == [
        {"party": "R", "yea": 1, "nay": 0, "present": 0, "notVoting": 1},
        {"party": "D", "yea": 1, "nay": 1, "present": 0, "notVoting": 0},
    ]
    assert r["congressGovUrl"] == "https://www.congress.gov/bill/119th-congress/senate-bill/4668"
    assert r["unavailable"] == []


def test_a_failed_part_is_named_and_not_cached(senate, monkeypatch):
    fake, calls = _answers(fail={"cosponsors"})
    monkeypatch.setattr(br, "_congress_get", fake)
    raw = asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668"))
    assert raw["unavailable"] == ["cosponsors"]
    assert br.shape_record(senate, 119, "S.4668", raw)["cosponsors"] == []
    n = len(calls)
    asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668"))
    # Everything else came from the cache; only the failed part was asked again.
    assert len(calls) == n + 1 and "/cosponsors" in calls[-1]


def test_no_such_bill(senate, monkeypatch):
    fake, _ = _answers(missing=True)
    monkeypatch.setattr(br, "_congress_get", fake)
    assert asyncio.run(br.fetch_bill_record(None, senate, 119, "S.99999"))["not_found"] is True


def test_a_missing_bill_is_cached_so_asking_again_costs_nothing(senate, monkeypatch):
    # Probing wrong ids used to go to Congress.gov every time.
    fake, calls = _answers(missing=True)
    monkeypatch.setattr(br, "_congress_get", fake)
    asyncio.run(br.fetch_bill_record(None, senate, 119, "S.99999"))
    n = len(calls)
    assert asyncio.run(br.fetch_bill_record(None, senate, 119, "S.99999"))["not_found"] is True
    assert len(calls) == n


def test_only_cache_misses_are_charged_before_anything_is_fetched(senate, monkeypatch):
    fake, calls = _answers()
    monkeypatch.setattr(br, "_congress_get", fake)
    charged = []
    asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", spend=charged.append))
    asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", spend=charged.append))
    assert charged == [5, 0]

    def refuse(n):
        raise RuntimeError("budget spent")
    monkeypatch.setattr(br, "_congress_get", fake)
    n = len(calls)
    with pytest.raises(RuntimeError):
        asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4669", spend=refuse))
    assert len(calls) == n  # refused before any request went out


def test_congress_gov_url_ordinal(senate):
    # "93th" / "101th" were dead links.
    assert br.shape_record(senate, 101, "HR.1", {})["congressGovUrl"] == (
        "https://www.congress.gov/bill/101st-congress/house-bill/1")
    assert br.shape_record(senate, 93, "S.2", {})["congressGovUrl"] == (
        "https://www.congress.gov/bill/93rd-congress/senate-bill/2")


def test_vote_detail_links_senators_by_name_and_state(senate):
    rc = senate.query(RollCall).one()
    v = br.vote_detail(senate, rc)
    pages = {m["lastName"]: m["page"] for m in v["members"]}
    # "Lujan" in the Senate's file is "Ben Ray Luján" on the site.
    assert pages == {"Cantwell": "/politicians/maria-cantwell", "Cruz": "/politicians/ted-cruz",
                     "Lujan": "/politicians/ben-ray-lujan", "Tillis": None}
    assert {m["bucket"] for m in v["members"]} == {"yea", "nay", "notVoting"}


@pytest.mark.parametrize("bill_id,parsed", [
    ("S.4668", ("s", 4668)), ("HCONRES.89", ("hconres", 89)), ("PN.12", None), ("S4668", None),
])
def test_parse_bill_id(bill_id, parsed):
    assert br.parse_bill_id(bill_id) == parsed


class TestRoutes:
    @pytest.fixture
    def client(self, senate, monkeypatch):
        fake, _ = _answers()
        monkeypatch.setattr(br, "_congress_get", fake)
        app.dependency_overrides[get_db] = lambda: senate
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_record(self, client):
        r = client.get("/api/bills/S.4668/record?congress=119")
        assert r.status_code == 200 and r.json()["votes"][0]["number"] == 243

    def test_not_a_bill_id(self, client):
        assert client.get("/api/bills/PN.12/record").status_code == 404

    def test_a_congress_that_has_not_convened_is_refused(self, client):
        assert client.get("/api/bills/S.1/record?congress=200").status_code == 404

    def test_upstream_budget_and_per_ip_limit(self, client, monkeypatch):
        from app.api import rate_limit

        rate_limit.reset_upstream_budget()
        monkeypatch.setattr(rate_limit, "_UPSTREAM_CALLS_PER_HOUR", 7)
        assert client.get("/api/bills/S.4668/record?congress=119").status_code == 200  # 5 calls
        r = client.get("/api/bills/S.4669/record?congress=119")  # 5 more don't fit
        assert r.status_code == 503 and r.headers["Retry-After"]
        # A cached answer still serves once the budget is spent.
        assert client.get("/api/bills/S.4668/record?congress=119").status_code == 200
        codes = [client.get("/api/bills/S.4668/record?congress=119").status_code for _ in range(10)]
        assert 429 in codes
        rate_limit.reset_upstream_budget()

    def test_vote(self, client):
        r = client.get("/api/congress/votes/senate/119/2/243")
        assert r.status_code == 200 and len(r.json()["members"]) == 4
        assert client.get("/api/congress/votes/senate/119/2/9").status_code == 404


@pytest.mark.parametrize("person,name", [
    ({"fullName": "Sen. Cruz, Ted [R-TX]"}, "Ted Cruz"),
    ({"fullName": "Rep. Van Orden, Derrick [R-WI-3]"}, "Derrick Van Orden"),
    ({"firstName": "Christopher", "middleName": "A.", "lastName": "Coons", "fullName": "Sen. Coons, Christopher A. [D-DE]"}, "Christopher A. Coons"),
])
def test_display_name(person, name):
    assert br.display_name(person) == name
