"""The whole House run (house_pipeline.run_house_pipeline) end to end, on
a small chamber: six representatives, four key bills (three with House
roll calls), recent Clerk roll calls, cosponsors, sponsored bills, and one
member's FEC record with PAC receipts. Every source is a stub returning the
shape its reader documents, and the embedding model is the deterministic
stand-in in pipeline_run_helpers, so every classifier runs its own code.

The earlier House tests stop at the first fetch or run the run's start with
empty bills; none reached the scoring pass. This one checks the run
completes, every member is scored and stored, and the stored results stay
what they were (tests/fixtures/house_run_golden.json), so a change to how
the run is organised can be shown to change nothing."""

import asyncio
import json
import pathlib
from datetime import datetime

import pytest

from app.models import HousePipelineRun, PipelineStatus, Representative, ScoreSnapshot
from app.pipeline import house_pipeline as hp
from tests.pipeline_run_helpers import matches_golden, run_on_test_database

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "house_run_golden.json"
CONGRESS = 119
YEAR = 2025

REPS = [
    ("A000101", "Adler, Ann", "Democratic", "California", 12, "D"),
    ("B000102", "Burke, Ben", "Republican", "Texas", 7, "R"),
    ("C000103", "Cole, Cara", "Democratic", "New York", 10, "D"),
    ("D000104", "Dunn, Dale", "Republican", "Florida", 3, "R"),
    ("E000105", "Ellis, Eva", "Democratic", "Illinois", 5, "D"),
    ("F000106", "Frye, Fred", "Republican", "Ohio", 9, "R"),
]
PARTY = {bio: p for bio, *_, p in REPS}
STATE_CODE = {"California": "CA", "Texas": "TX", "New York": "NY", "Florida": "FL", "Illinois": "IL", "Ohio": "OH"}


def _member(bio, name, party_name, state, district, _p):
    return {"bioguideId": bio, "name": name, "partyName": party_name, "state": state, "district": district,
            "terms": {"item": [{"chamber": "House of Representatives", "startYear": 2019, "district": district}]}}


def _detail(bio, name, party_name, state, district, _p):
    return {"bioguideId": bio, "lastName": name.split(",")[0], "state": state,
            "terms": [{"chamber": "House of Representatives", "startYear": 2019, "district": district,
                       "stateCode": STATE_CODE[state]}],
            "officialWebsiteUrl": f"https://{name.split(',')[0].lower()}.house.gov/",
            "addressInformation": {"officeAddress": "1 Longworth House Office Building",
                                   "phoneNumber": "(202) 225-0000"}}


# Four key bills; each passed the House on a recorded vote but H.R. 4.
BILLS = {
    1: ("Clean Water Infrastructure Act", "A bill to fund water treatment and drinking water systems", "D", 21),
    2: ("Border Security Enforcement Act", "A bill to increase border patrol staffing and enforcement", "R", 22),
    3: ("Veterans Health Access Act", "A bill to expand health care access for veterans", "D", 23),
    4: ("Small Business Tax Relief Act", "A bill to reduce taxes on small businesses", "R", None),
}
# Party-line except the named breaks: {roll number: {bioguide id: vote}}.
BREAKS = {22: {"A000101": "Yea"}, 31: {"F000106": "Yea"}}


def _roll_call(number, question, document, title, majority, date):
    members = []
    for bio, name, _pn, state, _d, party in REPS:
        vote = "Yea" if party == majority else "Nay"
        vote = BREAKS.get(number, {}).get(bio, vote)
        members.append({"bioguideId": bio, "lastName": name.split(",")[0], "firstName": "", "party": party,
                        "state": STATE_CODE[state], "voteCast": vote})
    yeas = sum(m["voteCast"] == "Yea" for m in members)
    result = "Passed" if yeas > 3 else "Failed"
    return {"year": YEAR, "congress": CONGRESS, "session": 1, "rollNumber": number, "voteTitle": title,
            "voteDate": date, "question": question, "documentTitle": title, "documentName": document,
            "amendment": "", "result": result, "rejected": yeas <= 3, "majorityRequirement": "1/2",
            "members": members, "chamber": "House"}


KEY_ROLL_CALLS = {
    rc: _roll_call(rc, "On Passage", f"H R {n}", BILLS[n][0], BILLS[n][2], f"{YEAR}-03-0{n}")
    for n, (*_, rc) in BILLS.items() if rc
}
RECENT = [
    _roll_call(30, "On Motion to Suspend the Rules and Pass", "H R 30", "Energy Grid Modernization Act", "D",
               f"{YEAR}-06-03"),
    _roll_call(31, "On Passage", "H R 31", "Farm Credit Expansion Act", "R", f"{YEAR}-06-04"),
    _roll_call(32, "On Ordering the Previous Question", "H RES 32", "Providing for consideration of H.R. 31",
               "R", f"{YEAR}-06-05"),
]


def _actions(n):
    rc = BILLS[n][3]
    if rc is None:
        return [{"actionDate": f"{YEAR}-02-01", "text": "Referred to the Committee on Ways and Means."}]
    return [{"actionDate": f"{YEAR}-03-0{n}", "text": f"On passage Passed by the Yeas and Nays: (Roll no. {rc}).",
             "recordedVotes": [{"chamber": "House", "rollNumber": rc,
                                "url": f"https://clerk.house.gov/evs/{YEAR}/roll0{rc}.xml"}]},
            {"actionDate": f"{YEAR}-02-01", "text": "Referred to the House Committee."}]


def _cosponsors(n):
    """Every member of the sponsor's party but the sponsor, and on H.R. 3
    one of the other party."""
    sponsor = list(PARTY)[n - 1]
    out = [{"bioguideId": b, "party": p} for b, p in PARTY.items() if p == PARTY[sponsor] and b != sponsor]
    if n == 3:
        out.append({"bioguideId": "F000106", "party": "R"})
    return out


def _sponsored(bio):
    i = list(PARTY).index(bio)
    return [
        {"type": "HR", "number": str(100 + i), "congress": CONGRESS, "title": f"Rural Hospital Support Act {i}",
         "introducedDate": f"{YEAR}-02-10", "policyArea": {"name": "Health"},
         "latestAction": {"actionDate": f"{YEAR}-02-10", "text": "Referred to the Committee on Energy and Commerce."}},
        {"type": "HR", "number": str(200 + i), "congress": CONGRESS,
         "title": f"Highway Safety Improvement Act of {YEAR} number {i}",
         "introducedDate": f"{YEAR}-03-01", "policyArea": {"name": "Transportation and Public Works"},
         "latestAction": {"actionDate": f"{YEAR}-05-01", "text": "Became Public Law No: 119-12." if i == 0
                          else "Passed/agreed to in House: On motion to suspend the rules and pass the bill."}},
    ]


def _sponsored_actions(n):
    i = n % 100
    if n >= 200 and i == 0:
        return [{"actionDate": f"{YEAR}-05-01", "text": "Became Public Law No: 119-12."},
                {"actionDate": f"{YEAR}-04-01", "text": "Passed Senate without amendment by Unanimous Consent."},
                {"actionDate": f"{YEAR}-03-15", "text": "On passage Passed by voice vote."}]
    if n >= 200:
        return [{"actionDate": f"{YEAR}-05-01", "text": "On motion to suspend the rules and pass the bill Agreed to by voice vote."}]
    return [{"actionDate": f"{YEAR}-02-10", "text": "Referred to the Committee on Energy and Commerce."}]


ADLER_FEC = {
    "candidate": {"candidate_id": "H8CA12001", "name": "ADLER, ANN", "office": "H", "state": "CA", "district": "12"},
    "financials": [{"cycle": 2024, "candidate_election_year": 2024, "receipts": 1_000_000,
                    "contributions": 900_000, "individual_contributions": 700_000,
                    "individual_itemized_contributions": 500_000, "individual_unitemized_contributions": 200_000,
                    "other_political_committee_contributions": 200_000, "coverage_end_date": "2024-12-31"}],
    "pacReceipts": [
        {"contributor_name": "NATIONAL NURSES UNITED PAC", "contributor_id": "C00000001", "entity_type": "PAC",
         "contribution_receipt_amount": 5000, "contribution_receipt_date": "2024-03-01",
         "committee": {"name": "ADLER FOR CONGRESS"}},
        {"contributor_name": "ACME WIDGETS INC PAC", "contributor_id": "C00000002", "entity_type": "PAC",
         "contribution_receipt_amount": 2500, "contribution_receipt_date": "2024-04-01",
         "committee": {"name": "ADLER FOR CONGRESS"}},
    ],
}


@pytest.fixture
def chamber(db_session, monkeypatch, freeze_utcnow):
    freeze_utcnow(datetime(YEAR, 7, 1, 15))
    run_on_test_database(monkeypatch, db_session, hp)

    async def ret(value):
        return value

    def stub(name, fn):
        monkeypatch.setattr(hp, name, fn)

    details = {r[0]: _detail(*r) for r in REPS}
    stub("fetch_representatives", lambda client, db: ret([_member(*r) for r in REPS]))
    stub("fetch_member_detail", lambda client, db, bio: ret(details[bio]))
    stub("fetch_house_sworn_dates", lambda client, db: ret({bio: f"{YEAR}-01-03" for bio in PARTY}))
    stub("fetch_house_caucuses", lambda client, db: ret({}))
    stub("fetch_significant_bills", lambda client, db, max_bills: ret([
        {"congress": CONGRESS, "type": "hr", "number": n, "name": BILLS[n][0],
         "sponsorBioguide": list(PARTY)[n - 1], "sponsorParty": BILLS[n][2]} for n in BILLS]))

    async def actions(client, db, cg, t, n, *a, **k):
        if n in BILLS:
            return _actions(n)
        return _sponsored_actions(n)

    stub("fetch_bill_actions", actions)
    stub("fetch_house_roll_call_vote", lambda client, db, year, number, *a, **k: ret(KEY_ROLL_CALLS.get(number)))
    stub("fetch_recent_house_roll_calls", lambda client, db, year, count, **k: ret(RECENT if year == YEAR else []))
    stub("fetch_bill_summaries", lambda client, db, cg, t, n: ret([{"text": BILLS[n][1] + "."}]))
    stub("fetch_bill_titles_or_none", lambda client, db, cg, t, n: ret(
        [{"titleTypeCode": 6, "title": f"A bill numbered {n} to improve public services"}]))
    stub("fetch_bill_cosponsors", lambda client, db, cg, t, n: ret(
        _cosponsors(n) if n in BILLS else [{"bioguideId": "B000102", "party": "R"}]))
    stub("fetch_member_sponsored", lambda client, db, bio: ret(_sponsored(bio)))

    async def find_candidate(client, db, name, state, office=None, district=None, bioguide_id=None):
        return ADLER_FEC["candidate"] if bioguide_id == "A000101" else None

    stub("find_candidate", find_candidate)
    stub("fetch_candidate_financials", lambda client, db, cand: ret(ADLER_FEC["financials"]))
    stub("fetch_candidate_committees", lambda client, db, cand: ret([{"committee_id": "C00999999"}]))
    stub("with_seat_election", lambda client, db, financials, committees, year: ret(financials))
    stub("fetch_committee_receipts", lambda client, db, comm, cycles=None: ret([]))
    stub("fetch_pac_receipts", lambda client, db, comm, cycles=None: ret(ADLER_FEC["pacReceipts"]))
    stub("fetch_committee_master", lambda client, db, cycles: ret({}))
    stub("fetch_committee_contributions", lambda client, db, cycles: ret({}))
    stub("resolve_committee_meta", lambda client, db, ids, master, *a: ret({}))
    stub("fetch_contribution_detail", lambda *a, **k: ret(None))
    stub("enrich_lobbying_matches_with_lda", lambda matches, db, year, votes=None: ret({}))
    stub("refresh_member_ideal_points", lambda chamber, congress: ret(None))
    return db_session


def test_the_whole_run_scores_and_stores_every_member(chamber):
    result = asyncio.run(hp.run_house_pipeline())
    run = chamber.query(HousePipelineRun).one()
    assert result["status"] == PipelineStatus.COMPLETED, run.error_detail or result
    assert (result["reps_processed"], result["reps_failed"]) == (6, 0)
    assert chamber.query(Representative).count() == 6
    assert chamber.query(ScoreSnapshot).count() == 6
    adler = chamber.query(Representative).filter_by(id="ann-adler").one()
    assert adler.score_funding_independence != 50.0  # her FEC record was read and scored
    assert json.loads(adler.score_confidence)  # scored, with its confidence
    # Everything the run stored is what it stored before
    # (PIPELINE_RUN_GOLDEN=write regenerates it, for a change meant to
    # move the results).
    assert matches_golden(chamber, GOLDEN)


def test_unread_sources_degrade_only_what_they_feed(chamber, monkeypatch):
    """Each failed read named in its own way: a key bill's summary, titles
    and actions, a sponsored bill's actions, a bill's cosponsors, one
    member's sponsored bills, the Clerk's sworn dates and caucuses, and one
    member's FEC record; and one member whose scoring raises. The run
    finishes as partial; the members it couldn't prepare or score keep
    their stored record (none here), the rest are scored."""
    from app.pipeline.fetch.congress import CongressUnavailable
    from app.pipeline.fetch.fec import FecUnavailable

    async def ret(value):
        return value

    async def actions(client, db, cg, t, n, *a, **k):
        if n in (4, 201):
            raise CongressUnavailable("actions unreadable")
        return _actions(n) if n in BILLS else _sponsored_actions(n)

    async def find_candidate(client, db, name, state, office=None, district=None, bioguide_id=None):
        if bioguide_id == "B000102":
            raise FecUnavailable("FEC unreachable")
        return ADLER_FEC["candidate"] if bioguide_id == "A000101" else None

    scores = hp.calculate_scores

    def scoring_failing_for_frye(rep):
        if rep.get("bioguideId") == "F000106":
            raise RuntimeError("scoring failed")
        return scores(rep)

    for name, fn in (
        ("fetch_bill_summaries", lambda client, db, cg, t, n: ret(None if n == 1 else [{"text": BILLS[n][1]}])),
        ("fetch_bill_titles_or_none", lambda client, db, cg, t, n: ret(None if n == 2 else [])),
        ("fetch_bill_actions", actions),
        ("fetch_bill_cosponsors", lambda client, db, cg, t, n: ret(None if n == 3 else [])),
        ("fetch_member_sponsored", lambda client, db, bio: ret(None if bio == "E000105" else _sponsored(bio))),
        ("fetch_house_sworn_dates", lambda client, db: ret({})),
        ("fetch_house_caucuses", lambda client, db: ret(None)),
        ("find_candidate", find_candidate),
        ("calculate_scores", scoring_failing_for_frye),
    ):
        monkeypatch.setattr(hp, name, fn)
    result = asyncio.run(hp.run_house_pipeline())
    assert result["status"] == PipelineStatus.PARTIAL, result
    assert (result["reps_processed"], result["reps_failed"]) == (4, 2)
    assert sorted(r.id for r in chamber.query(Representative)) == ["ann-adler", "cara-cole", "dale-dunn", "eva-ellis"]
    run = chamber.query(HousePipelineRun).one()
    assert run.reps_failed == 2 and "2 of 6 reps failed" in run.error_message
    # Ellis's sponsored bills weren't read: scored without them, not as none.
    ellis = chamber.query(Representative).filter_by(id="eva-ellis").one()
    assert json.loads(ellis.score_confidence)["legislativeEffectivenessBills"] == "unavailable"


def test_unreadable_clerk_roll_calls_fail_the_run_before_anyone_is_written(chamber, monkeypatch):
    async def none(*a, **k):
        return None

    monkeypatch.setattr(hp, "fetch_recent_house_roll_calls", none)
    result = asyncio.run(hp.run_house_pipeline())
    assert result["status"] == PipelineStatus.FAILED
    assert "clerk.house.gov" in result["error"]
    assert chamber.query(Representative).count() == 0


def test_no_members_is_no_data(chamber, monkeypatch):
    async def nobody(client, db):
        return []

    monkeypatch.setattr(hp, "fetch_representatives", nobody)
    assert asyncio.run(hp.run_house_pipeline())["status"] == "no_data"
    assert chamber.query(HousePipelineRun).one().status == PipelineStatus.FAILED
