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
        "sponsors": [{"bioguideId": "C001098", "fullName": "Sen. Delgado, Rob [R-TX]", "party": "R", "state": "TX"}],
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
COSPONSORS = [{"bioguideId": "C000127", "fullName": "Sen. Bellweather, Maria [D-WA]", "party": "D", "state": "WA",
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
    db_session.add(Senator(id="rob-delgado", bioguide_id="C001098", name="Rob Delgado", state="TX", party="R"))
    db_session.add(Senator(id="nora-bellweather", bioguide_id="C000127", name="Nora Bellweather", state="WA", party="D"))
    db_session.add(Senator(id="ben-ray-montano", bioguide_id="L000570", name="Ben Ray Montaño", state="NM", party="D"))
    rc = RollCall(chamber="senate", congress=119, session=2, number=243, date="2026-09-24",
                  question="On the Cloture Motion S. 4668", result="Cloture Motion Agreed to",
                  yeas=2, nays=1, not_voting=1, bill_id="S.4668")
    db_session.add(rc)
    db_session.flush()
    for last, party, state, pos in (("Delgado", "R", "TX", "Yea"), ("Bellweather", "D", "WA", "Yea"),
                                    ("Montano", "D", "NM", "Nay"), ("Tillis", "R", "NC", "Not Voting")):
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
    assert r["sponsors"][0]["page"] == "/politicians/rob-delgado"
    assert r["cosponsors"][0]["page"] == "/politicians/nora-bellweather"
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


def test_a_part_that_misses_the_deadline_is_unavailable_and_asked_again(senate, monkeypatch):
    # The limiter is shared with the nightly pipeline: a part still waiting
    # when the reader's deadline passes is served as unavailable, never as
    # empty, and is not cached.
    fast, calls = _answers()

    async def slow_cosponsors(client, url):
        if "/cosponsors" in url:
            await asyncio.sleep(5)
        return await fast(client, url)

    monkeypatch.setattr(br, "_congress_get", slow_cosponsors)
    raw = asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", deadline_s=0.5))
    # Cosponsors ran out the clock, and the part after it was never asked.
    assert raw["unavailable"] == ["cosponsors", "text"]
    assert raw["bill"] is not None

    monkeypatch.setattr(br, "_congress_get", fast)
    n = len(calls)
    raw = asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", deadline_s=0.5))
    assert raw["unavailable"] == []
    # Only those two are asked again; the rest was cached.
    assert len(calls) == n + 2 and "/cosponsors" in calls[-2] and "/text" in calls[-1]


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
    asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", spend=_charging(charged)))
    asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", spend=_charging(charged)))
    assert charged == [1, 4]  # the bill, then the rest; the cached call charges nothing

    async def refuse(n):
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
    # "Montano" in the Senate's file is "Ben Ray Montaño" on the site.
    assert pages == {"Bellweather": "/politicians/nora-bellweather", "Delgado": "/politicians/rob-delgado",
                     "Montano": "/politicians/ben-ray-montano", "Tillis": None}
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

    def test_a_complete_record_is_served_and_cacheable(self, client):
        r = client.get("/api/bills/S.4668/record?congress=119")
        assert r.status_code == 200 and r.json()["votes"][0]["number"] == 243
        assert r.headers["Cache-Control"].startswith("public, max-age=")

    def test_a_partial_record_is_not_kept_by_any_cache(self, client, monkeypatch):
        fake, _ = _answers(fail={"text"})
        monkeypatch.setattr(br, "_congress_get", fake)
        r = client.get("/api/bills/S.4668/record?congress=119")
        assert r.status_code == 200 and r.json()["unavailable"] == ["text"]
        assert r.headers["Cache-Control"] == "no-store"

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
    ({"fullName": "Sen. Delgado, Rob [R-TX]"}, "Rob Delgado"),
    ({"fullName": "Rep. Van Aken, Dorian [R-WI-3]"}, "Dorian Van Aken"),
    ({"firstName": "Christopher", "middleName": "A.", "lastName": "Combs", "fullName": "Sen. Combs, Christopher A. [D-DE]"}, "Christopher A. Combs"),
])
def test_display_name(person, name):
    assert br.display_name(person) == name


async def test_parts_fetched_before_a_cancellation_are_still_cached(db_session, monkeypatch):
    """The budget was spent on them: a reader who leaves mid-fetch mustn't
    make the next one pay again."""
    import asyncio

    from app.services import bill_record

    calls = []

    async def congress_get(client, url):
        calls.append(url)
        if len(calls) == 2:
            await asyncio.sleep(10)  # the reader leaves here
        return {"bill": {"number": "1"}, "summaries": [], "actions": [], "cosponsors": [], "textVersions": []}

    monkeypatch.setattr(bill_record, "_congress_get", congress_get)
    written = []

    async def write_many(db, tier, items, **kw):
        written.append(dict(items))

    monkeypatch.setattr(bill_record, "api_cache_set_many_async", write_many)
    task = asyncio.create_task(bill_record.fetch_bill_record(None, db_session, 119, "S.4668"))
    for _ in range(300):
        if len(calls) >= 2 or task.done():
            break
        await asyncio.sleep(0.01)
    assert len(calls) == 2
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert written and len(written[0]) == 1  # the first part, fetched before the cancel


def test_a_wrong_id_is_charged_only_for_the_one_request_it_makes(senate, monkeypatch):
    fake, calls = _answers(missing=True)
    monkeypatch.setattr(br, "_congress_get", fake)
    charged = []
    raw = asyncio.run(br.fetch_bill_record(None, senate, 119, "S.99999", spend=_charging(charged)))
    assert raw["not_found"] is True
    assert charged == [1]


def _charging(charged: list):
    """An async spend callback (as rate_limit.spend_upstream is) recording
    what it was charged."""
    async def spend(n):
        charged.append(n)
    return spend


def test_a_failed_bill_request_stops_there(senate, monkeypatch):
    """An outage: the other four requests would fail the same way, and the
    budget must not be charged for them."""
    fake, calls = _answers(fail={"bill"})
    monkeypatch.setattr(br, "_congress_get", fake)
    charged = []
    raw = asyncio.run(br.fetch_bill_record(None, senate, 119, "S.4668", spend=_charging(charged)))
    assert len(calls) == 1 and charged == [1]
    assert set(raw["unavailable"]) == {"bill", "summaries", "actions", "cosponsors", "text"}


def test_a_successor_of_the_same_surname_is_not_linked_to_the_predecessors_votes(db_session):
    """Darline Graham took Lindsey Graham's seat; the vote pages linked his
    roll-call positions to her page (live, 2026-10-03)."""
    db_session.add(Senator(id="darline-graham", bioguide_id="G000600", name="Darline Graham", state="SC", party="R"))
    for number, first, lis in ((624, "Lindsey", "S293"), (254, "Darline", "S441")):
        rc = RollCall(chamber="senate", congress=119, session=1, number=number, date="2026-01-01", question="Q")
        db_session.add(rc)
        db_session.flush()
        db_session.add(RollCallPosition(roll_call_id=rc.id, member_id=lis, last_name="Graham", first_name=first,
                                        party="R", state="SC", position="Yea"))
    db_session.commit()
    pages = {rc.number: br.vote_detail(db_session, rc)["members"][0]["page"] for rc in db_session.query(RollCall)}
    assert pages == {624: None, 254: "/politicians/darline-graham"}
