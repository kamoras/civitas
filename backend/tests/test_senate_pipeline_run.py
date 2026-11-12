"""The whole Senate run (senate_pipeline.run_senate_pipeline) end to end,
on a small chamber: six senators, four key bills with Senate roll calls,
recent roll calls, cosponsors, sponsored bills, and one senator's FEC
record. Every source is a stub returning the shape its reader documents,
and the embedding model is a deterministic stand-in (hashed bag of
words), so every classifier runs its own code on stable vectors.

It exists because nothing else ran the body of the run: every earlier
test stopped at the run lock or the first fetch. What it checks is that
the run completes, every senator is scored and stored, and the stored
results stay what they were (a summary pinned below), so a change to how
the run is organised can be shown to change nothing."""

import asyncio
import hashlib
import json
import pathlib
import re
from datetime import datetime

import os

import numpy as np
import pytest

from app.config import settings
from app.models import PipelineRun, PipelineStatus, ScoreSnapshot, Senator
from app.pipeline import senate_pipeline as sp
from app.pipeline import vector_store

_DIM = 384


class _HashedWords:
    """A stand-in sentence-transformer: each word hashed into a fixed
    vector, summed and normalised, so texts sharing words are near."""

    max_seq_length = 512

    def __init__(self, name=None, *args, **kwargs):
        self.name = name

        class _Tok:
            def num_special_tokens_to_add(self, *a, **k):
                return 0

            def __call__(self, text, *a, **k):
                return {"input_ids": list(range(len(str(text).split())))}

        self.tokenizer = _Tok()

    def get_sentence_embedding_dimension(self):
        return _DIM

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(_DIM)
        for word in re.findall(r"[a-z0-9]+", str(text).lower()):
            seed = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "little")
            v += np.random.default_rng(seed).standard_normal(_DIM)
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode(self, texts, *args, normalize_embeddings=False, convert_to_numpy=True, **kwargs):
        if isinstance(texts, str):
            return self._one(texts)
        return np.array([self._one(t) for t in texts]) if len(texts) else np.zeros((0, _DIM))


# ── The chamber ────────────────────────────────────────────────────

SENATORS = [
    ("A000001", "Adams, Alice", "Democratic", "California", "CA", "D"),
    ("B000002", "Brown, Bob", "Republican", "Texas", "TX", "R"),
    ("C000003", "Clark, Carol", "Democratic", "New York", "NY", "D"),
    ("D000004", "Davis, Dan", "Republican", "Florida", "FL", "R"),
    ("E000005", "Evans, Erin", "Democratic", "Illinois", "IL", "D"),
    ("F000006", "Foster, Frank", "Republican", "Ohio", "OH", "R"),
]
PARTY = {bio: p for bio, *_, p in SENATORS}
CONGRESS = 119


def _member(bio, name, party_name, state_name, *_):
    return {"bioguideId": bio, "name": name, "partyName": party_name, "state": state_name,
            "terms": {"item": [{"chamber": "Senate", "startYear": 2019}]}}


def _detail(bio, name, party_name, state_name, code, _p):
    return {"bioguideId": bio, "lastName": name.split(",")[0], "state": state_name,
            "terms": [{"chamber": "Senate", "startYear": 2019, "stateCode": code}],
            "officialWebsiteUrl": f"https://www.{name.split(',')[0].lower()}.senate.gov/",
            "addressInformation": {"officeAddress": "1 Senate Office Building", "phoneNumber": "(202) 224-0000"}}


# Four key bills; each passed the Senate on a recorded vote but S.4.
BILLS = {
    1: ("Clean Water Infrastructure Act", "A bill to fund water treatment and drinking water systems",
        "Environmental Protection", "D", 1),
    2: ("Border Security Enforcement Act", "A bill to increase border patrol staffing and enforcement",
        "Immigration", "R", 2),
    3: ("Veterans Health Access Act", "A bill to expand health care access for veterans",
        "Armed Forces and National Security", "D", 3),
    4: ("Small Business Tax Relief Act", "A bill to reduce taxes on small businesses",
        "Taxation", "R", None),
}

# Party-line except the named breaks: {roll number: {bioguide id: vote}}.
BREAKS = {2: {"A000001": "Yea"}, 11: {"F000006": "Yea"}}


def _roll_call(number, question, document, title, majority, date):
    members = []
    for bio, name, _pn, _sn, code, party in SENATORS:
        vote = "Yea" if party == majority else "Nay"
        vote = BREAKS.get(number, {}).get(bio, vote)
        last, first = name.split(", ")
        members.append({"firstName": first, "lastName": last, "party": party, "state": code,
                        "voteCast": vote, "lisId": f"S{bio[1:4]}"})
    yeas = sum(m["voteCast"] == "Yea" for m in members)
    return {"congress": CONGRESS, "session": 1, "rollNumber": number, "tieBreaker": None,
            "voteTitle": title, "voteDate": date, "question": question, "documentTitle": title,
            "documentName": document, "result": "Bill Passed" if yeas > 3 else "Bill Defeated",
            "resultText": "", "majorityRequirement": "1/2", "rejected": yeas <= 3, "members": members}


KEY_ROLL_CALLS = {
    n: _roll_call(rc, "On Passage of the Bill", f"S. {n}", BILLS[n][0], BILLS[n][3],
                  f"March {n}, 2025,  02:00 PM")
    for n, (*_, rc) in BILLS.items() if rc
}
RECENT = [
    _roll_call(10, "On the Cloture Motion", "S. 10", "Energy Grid Modernization Act", "D", "June 3, 2025,  11:00 AM"),
    _roll_call(11, "On Passage of the Bill", "S. 11", "Farm Credit Expansion Act", "R", "June 4, 2025,  03:00 PM"),
    _roll_call(12, "On the Nomination", "PN12", "Jane Roe, of Virginia, to be a Judge", "R",
               "June 5, 2025,  10:00 AM"),
]


def _actions(n):
    rc = BILLS[n][4]
    if rc is None:
        return [{"actionDate": "2025-02-01", "text": "Read twice and referred to the Committee on Finance."}]
    return [{"actionDate": f"2025-03-0{n}", "text": "Passed Senate without amendment by Yea-Nay Vote.",
             "recordedVotes": [{"chamber": "Senate", "congress": CONGRESS, "sessionNumber": 1, "rollNumber": rc}]},
            {"actionDate": "2025-02-01", "text": "Read twice and referred to the committee."}]


def _cosponsors(n):
    """Every senator of the sponsor's party but the sponsor, and on S.3 one
    of the other party."""
    sponsor = list(PARTY)[n - 1]
    out = [{"bioguideId": b, "party": p} for b, p in PARTY.items() if p == PARTY[sponsor] and b != sponsor]
    if n == 3:
        out.append({"bioguideId": "F000006", "party": "R"})
    return out


def _sponsored(bio):
    i = list(PARTY).index(bio)
    return [
        {"type": "S", "number": str(100 + i), "congress": CONGRESS, "title": f"Rural Hospital Support Act {i}",
         "introducedDate": "2025-02-10", "policyArea": {"name": "Health"},
         "latestAction": {"actionDate": "2025-02-10", "text": "Read twice and referred to the Committee on Finance."}},
        {"type": "S", "number": str(200 + i), "congress": CONGRESS,
         "title": f"Highway Safety Improvement Act of 2025 number {i}",
         "introducedDate": "2025-03-01", "policyArea": {"name": "Transportation and Public Works"},
         "latestAction": {"actionDate": "2025-05-01", "text": "Became Public Law No: 119-12." if i == 0
                          else "Passed Senate without amendment by Unanimous Consent."}},
    ]


def _fec(senator):
    """One senator's FEC record (Adams's); the rest have no FEC match."""
    if senator["bioguideId"] != "A000001":
        return None
    return {
        "candidate": {"candidate_id": "S8CA00001", "name": "ADAMS, ALICE", "office": "S", "state": "CA"},
        "financials": [{"cycle": 2024, "candidate_election_year": 2024, "receipts": 1_000_000,
                        "contributions": 900_000, "individual_contributions": 700_000,
                        "individual_itemized_contributions": 500_000,
                        "individual_unitemized_contributions": 200_000,
                        "other_political_committee_contributions": 200_000, "coverage_end_date": "2024-12-31"}],
        "receipts": [],
        "pacReceipts": [
            {"contributor_name": "NATIONAL NURSES UNITED PAC", "contributor_id": "C00000001", "entity_type": "PAC",
             "contribution_receipt_amount": 5000, "contribution_receipt_date": "2024-03-01",
             "committee": {"name": "ADAMS FOR SENATE"}},
            {"contributor_name": "ACME WIDGETS INC PAC", "contributor_id": "C00000002", "entity_type": "PAC",
             "contribution_receipt_amount": 2500, "contribution_receipt_date": "2024-04-01",
             "committee": {"name": "ADAMS FOR SENATE"}},
        ],
        "detail": None,
    }


@pytest.fixture
def chamber(db_session, monkeypatch):
    from app import database

    monkeypatch.setattr(sp, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    # The modules that open their own session (ops alerts, the score
    # calibration check) reach the same in-memory database.
    monkeypatch.setitem(database.SessionLocal.kw, "bind", db_session.get_bind())
    monkeypatch.setattr(settings, "DATA_GOV_API_KEY", "test-key")
    monkeypatch.setattr(vector_store, "SentenceTransformer", _HashedWords)
    monkeypatch.setattr(vector_store, "_model", None)
    monkeypatch.setattr(vector_store, "_similarity_model", None)

    async def ret(value):
        return value

    def stub(name, fn):
        monkeypatch.setattr(sp, name, fn)

    stub("fetch_senators", lambda client, db: ret([_member(*s) for s in SENATORS]))
    details = {s[0]: _detail(*s) for s in SENATORS}
    stub("fetch_member_detail", lambda client, db, bio: ret(details[bio]))
    stub("fetch_significant_bills", lambda client, db, max_bills: ret(
        [{"congress": CONGRESS, "type": "s", "number": n, "name": BILLS[n][0]} for n in BILLS]))
    stub("fetch_bill", lambda client, db, cg, t, n: ret({
        "title": BILLS[n][1], "policyArea": {"name": BILLS[n][2]},
        "sponsors": [{"bioguideId": list(PARTY)[n - 1], "party": BILLS[n][3]}],
        "latestAction": {"text": _actions(n)[0]["text"]}}))
    stub("fetch_bill_summaries", lambda client, db, cg, t, n: ret([{"text": BILLS[n][1] + "."}]))

    async def actions(client, db, cg, t, n, *a, **k):
        return _actions(n) if t.lower() == "s" and n in BILLS else []

    stub("fetch_bill_actions", actions)
    # One short-title bill's titles can't be read (None): it is classified
    # from its short title, and the run goes on.
    stub("fetch_bill_titles_or_none", lambda client, db, cg, t, n: ret(
        None if n == 101 else [{"titleTypeCode": 6, "title": f"A bill numbered {n} to improve public services"}]))
    stub("fetch_bill_text", lambda client, db, cg, t, n: ret(None))
    stub("fetch_roll_call_vote", lambda client, db, cg, sess, number, *a, **k: ret(
        next((rc for rc in KEY_ROLL_CALLS.values() if rc["rollNumber"] == number), None)))
    stub("fetch_recent_roll_calls", lambda client, db, *, congress, session_number, count, **k: ret(
        RECENT if session_number == 1 else []))
    stub("fetch_bill_cosponsors", lambda client, db, cg, t, n: ret(
        _cosponsors(n) if n in BILLS else [{"bioguideId": "B000002", "party": "R"}]))
    stub("fetch_member_sponsored", lambda client, db, bio: ret(_sponsored(bio)))
    stub("fetch_committee_master", lambda client, db, cycles: ret({}))
    stub("fetch_committee_contributions", lambda client, db, cycles: ret({}))
    stub("_fetch_senator_fec", lambda client, db, senator, cc, cm: ret(_fec(senator)))
    stub("resolve_committee_meta", lambda client, db, ids, cm, unread: ret({}))
    stub("enrich_lobbying_matches_with_lda", lambda matches, db, year, votes=None: ret({}))
    from app.pipeline.fetch import voteview

    monkeypatch.setattr(voteview, "refresh_member_ideal_points", lambda chamber, congress: ret(None))
    return db_session


GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "senate_run_golden.json"
# Bookkeeping about the run itself, not what it stored about the chamber.
_NOT_STORED_RESULTS = {"pipeline_runs", "api_cache", "pipeline_progress", "source_read_stats",
                       "pipeline_phase_timings"}


def _plain(v):
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return _plain(json.loads(v))
        except ValueError:
            return v
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def stored(db) -> dict:
    """Every row of every table the run wrote, timestamps left out, in a
    stable order: what a reorganised run must reproduce exactly."""
    from datetime import date, datetime

    from app.database import Base

    out = {}
    for table in Base.metadata.sorted_tables:
        if table.name in _NOT_STORED_RESULTS:
            continue
        rows = []
        for row in db.execute(table.select()).mappings():
            rows.append({k: _plain(v) for k, v in row.items() if not isinstance(v, (datetime, date))})
        if rows:
            out[table.name] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def test_the_whole_run_scores_and_stores_every_senator(chamber, freeze_utcnow):
    freeze_utcnow(datetime(2025, 7, 1, 15))
    result = asyncio.run(sp.run_senate_pipeline())
    run = chamber.query(PipelineRun).one()
    assert result["status"] == PipelineStatus.COMPLETED, run.error_detail
    assert (result["senators_processed"], result["senators_failed"]) == (6, 0)
    assert run.status == PipelineStatus.COMPLETED and run.senators_failed == 0
    assert chamber.query(Senator).count() == 6
    assert chamber.query(ScoreSnapshot).count() == 6
    adams = chamber.query(Senator).filter_by(id="alice-adams").one()
    assert adams.score_funding_independence != 50.0  # her FEC record was read and scored
    # Everything the run stored is what it stored before: regenerate with
    # SENATE_RUN_GOLDEN=write only for a change meant to move the results.
    got = stored(chamber)
    if os.environ.get("SENATE_RUN_GOLDEN") == "write":
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    assert got == json.loads(GOLDEN.read_text())


def test_unread_sources_degrade_only_what_they_feed(chamber, monkeypatch, freeze_utcnow):
    """Each failed read named in its own way: a bill's summary, actions,
    text and cosponsors, one senator's sponsored bills, and one senator's
    FEC record. The run still completes; the senator whose FEC record
    couldn't be read keeps the stored record (a held score), the rest are
    scored."""
    from app.pipeline.fetch.congress import CongressUnavailable
    from app.pipeline.fetch.govinfo import GovinfoUnavailable

    freeze_utcnow(datetime(2025, 7, 1, 15))

    async def summaries(client, db, cg, t, n):
        return None if n == 1 else [{"text": BILLS[n][1] + "."}]

    async def actions(client, db, cg, t, n, *a, **k):
        if t.lower() == "s" and n == 4:
            raise CongressUnavailable("actions unreadable")
        return _actions(n) if t.lower() == "s" and n in BILLS else []

    async def text(client, db, cg, t, n):
        if n == 2:
            raise GovinfoUnavailable("text unreadable")
        return None

    async def cosponsors(client, db, cg, t, n):
        return None if n == 3 else (_cosponsors(n) if n in BILLS else [])

    async def sponsored(client, db, bio):
        return None if bio == "E000005" else _sponsored(bio)

    async def fec(client, db, senator, cc, cm):
        if senator["bioguideId"] == "B000002":
            return {"unavailable": True}
        return _fec(senator)

    analyze = sp.analyze_senator_batch

    async def analyze_failing_for_foster(inputs, **kwargs):
        if any(i["senator"]["name"] == "Frank Foster" for i in inputs):
            raise RuntimeError("analysis failed")
        return await analyze(inputs, **kwargs)

    monkeypatch.setattr(sp, "analyze_senator_batch", analyze_failing_for_foster)
    for name, fn in (("fetch_bill_summaries", summaries), ("fetch_bill_actions", actions),
                     ("fetch_bill_text", text), ("fetch_bill_cosponsors", cosponsors),
                     ("fetch_member_sponsored", sponsored), ("_fetch_senator_fec", fec)):
        monkeypatch.setattr(sp, name, fn)
    result = asyncio.run(sp.run_senate_pipeline())
    run = chamber.query(PipelineRun).one()
    assert result["status"] == PipelineStatus.COMPLETED, run.error_detail
    # Brown at prepare (his FEC record), Foster at analysis: neither had an
    # earlier record to keep, so neither is written.
    assert (result["senators_processed"], result["senators_failed"]) == (4, 2)
    assert sorted(s.id for s in chamber.query(Senator)) == ["alice-adams", "carol-clark", "dan-davis", "erin-evans"]
    assert run.senators_failed == 2
    # Evans's sponsored bills weren't read: scored without them, not as none.
    evans = chamber.query(Senator).filter_by(id="erin-evans").one()
    assert json.loads(evans.score_confidence)["legislativeEffectivenessBills"] == "unavailable"


def test_fetch_only_stops_before_the_analysis(chamber):
    result = asyncio.run(sp.run_senate_pipeline(fetch_only=True))
    assert result["status"] == PipelineStatus.COMPLETED
    assert chamber.query(PipelineRun).one().status == PipelineStatus.COMPLETED
    assert chamber.query(Senator).count() == 0


def test_a_filtered_run_scores_one_senator_and_retires_nobody(chamber):
    result = asyncio.run(sp.run_senate_pipeline(senator_filter="Clark"))
    assert result["status"] == PipelineStatus.COMPLETED
    assert (result["senators_processed"], result["senators_failed"]) == (1, 0)
    assert [s.id for s in chamber.query(Senator)] == ["carol-clark"]
    with pytest.raises(RuntimeError, match="Senator not found"):
        asyncio.run(sp.run_senate_pipeline(senator_filter="Nobody"))
