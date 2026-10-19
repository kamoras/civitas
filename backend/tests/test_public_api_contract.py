"""The public API's documentation is its contract (api/public.py).

Those routes return JSONResponse directly, which FastAPI never checks
against their response_model, so the published spec (/developers, from
/api/public/v1/openapi.json) could describe fields a response no longer
has, or miss ones it gained. This validates each endpoint's real body
against its documented schema; the Public* schemas forbid extra fields,
so drift in either direction fails here.
"""

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.api.router import api_router
from app.database import get_db
from app.models import (
    Agency,
    ExploreDocument,
    Justice,
    JusticeVote,
    Nomination,
    President,
    KeyVote,
    MemberIdAlias,
    PolicyStatement,
    PresidentialSupport,
    RepKeyVote,
    Representative,
    RollCall,
    RollCallPosition,
    ScoreSnapshot,
    Senator,
    StatementRollCall,
)
from app.pipeline.lexical_index import ensure_lexical_index
from app.schemas import (
    PublicAgencyDetailSchema,
    PublicAgencyListSchema,
    PublicApiIndexSchema,
    PublicBillVotesSchema,
    PublicCommentPeriodPageSchema,
    PublicJusticeCasesSchema,
    PublicJusticeListSchema,
    PublicJusticeSchema,
    PublicNominationDetailSchema,
    PublicNominationPageSchema,
    PublicPresidentListSchema,
    PublicPresidentSchema,
    PublicRollCallDetailSchema,
    PublicRollCallPageSchema,
    PublicDistrictLeanSchema,
    PublicHistorySchema,
    PublicMemberVotesSchema,
    PublicPresidentialSupportSchema,
    PublicRepresentativePageSchema,
    PublicRepresentativeProfileSchema,
    PublicScoreBreakdownSchema,
    PublicSearchResponseSchema,
    PublicSenatorPageSchema,
    PublicSenatorProfileSchema,
    PublicStateSchema,
)
from app.services import explore_search

SCORES = dict(
    score_funding_independence=60, score_promise_persistence=50, score_constituent_alignment=55,
    score_funding_diversity=40, score_legislative_effectiveness=70,
    score_confidence='{"fundingIndependence": "high"}',
)


@pytest.fixture
def client(db_session, fixed_ranking, monkeypatch):
    assert ensure_lexical_index(db_session.get_bind()), "FTS5 unavailable"
    # Semantic channel up with no hits: results come from the keyword
    # channel, through the real hybrid_search and its real result shape.
    monkeypatch.setattr(explore_search, "search_explore_documents", lambda *a, **k: [])
    db_session.add_all([
        Senator(id="S000001", name="Jane Doe", state="GA", party="D", **SCORES),
        Senator(id="S000002", name="Ann Poe", state="OH", party="R",
                **{**SCORES, "score_funding_independence": 95}),
        Representative(id="R000001", name="John Roe", state="GA", district=5, party="R", bioguide_id="R000001",
                       party_line_record='{"congress": 119, "votes": 1, "breaks": [{"rollCall": "house-119-1-7", '
                                         '"vote": "Yea"}], "flankBreaks": []}', **SCORES),
        Representative(id="R000002", name="Kay Loe", state="WY", district=0, party="D",
                       **{**SCORES, "score_funding_independence": 95}),
        ScoreSnapshot(entity_type="senator", entity_id="S000001", date="2026-09-01",
                      overall_score=58.2, score_1=60, score_2=50, score_3=55, score_4=40, score_5=70),
        ScoreSnapshot(entity_type="representative", entity_id="R000001", date="2026-09-01",
                      overall_score=58.2, score_1=60, score_2=50, score_3=55, score_4=40, score_5=70),
        ExploreDocument(doc_type="Final Rule", source="Federal Register", title="Wildfire smoke rule",
                        summary="Standards for wildfire smoke.", body="Wildfire smoke exposure.",
                        date="2026-01-01", chamber="Regulatory"),
        RollCall(id=1, chamber="senate", congress=119, session=1, number=5, date="2026-02-01",
                 question="On Passage of the Bill", result="Bill Passed", yeas=1, nays=0, bill_id="HR.1",
                 source_url="https://www.senate.gov/legislative/LIS/roll_call_votes/vote1191/vote_119_1_00005.htm"),
        RollCallPosition(roll_call_id=1, member_id="S001", last_name="Doe", party="D", state="GA", position="Yea"),
        # Two more of each party, so the roll call has a party split.
        *[RollCallPosition(roll_call_id=1, member_id=f"S{n}", last_name=f"D{n}", party="D", state="NY", position="Yea")
          for n in (10, 11)],
        *[RollCallPosition(roll_call_id=1, member_id=f"S{n}", last_name=f"R{n}", party="R", state="TX", position="Nay")
          for n in (20, 21, 22)],
        RollCall(id=7, chamber="house", congress=119, session=1, number=7, date="2026-01-20",
                 question="On Passage", result="Passed", yeas=4, nays=3, bill_id="HR.3",
                 source_url="https://clerk.house.gov/evs/2026/roll007.xml"),
        RollCallPosition(roll_call_id=7, member_id="R000001", last_name="Roe", party="R", state="GA", position="Aye"),
        *[RollCallPosition(roll_call_id=7, member_id=f"H{n}", last_name=f"D{n}", party="D", state="NY", position="Aye")
          for n in (1, 2, 3)],
        *[RollCallPosition(roll_call_id=7, member_id=f"H{n}", last_name=f"R{n}", party="R", state="TX", position="No")
          for n in (4, 5, 6)],
        KeyVote(senator_id="S000001", bill_name="Placeholder Act", bill_id="HR.1", date="2026-02-01", vote="Yea",
                voted_with_party=True, party_leaning="D", roll_call="senate-119-1-5"),
        RepKeyVote(representative_id="R000001", bill_name="Placeholder Act", bill_id="HR.1", date="2026-01-20",
                   vote="Nay", voted_with_party=None),
    ])
    statement = PolicyStatement(url="https://example.gov/sap.pdf", title="S. 2 – Placeholder Act", readable=True,
                                issued="2026-03-01", chamber="senate", measures='["S.2"]', amendments="[]",
                                position="support", position_sentence="The Administration supports passage of S. 2.")
    roll = RollCall(chamber="senate", congress=119, session=2, number=9, date="2026-03-04",
                    question="On Passage of the Bill S. 2", title="S. 2", bill_id="S.2", result="Bill Passed",
                    source_url="https://www.senate.gov/legislative/LIS/roll_call_votes/vote1192/vote_119_2_00009.xml")
    db_session.add_all([statement, roll])
    db_session.flush()
    db_session.add_all([
        StatementRollCall(roll_call_id=roll.id, statement_id=statement.id),
        PresidentialSupport(chamber="senate", member_id="S000001", congress=119, party="D", seat_alignment=0.2,
                            with_president=0, votes=1, expected=0.2),
        PresidentialSupport(chamber="senate", member_id="S000002", congress=119, party="R", seat_alignment=0.5,
                            with_president=1, votes=1, expected=None),
    ])
    db_session.add_all([
        President(id="example-2", name="Avery Example", party="F", number=2, term_start="1797-03-04",
                  term_end="1801-03-04", score_public_mandate=40, score_effectiveness=50, score_historical_legacy=60),
        President(id="sample-3", name="Blair Sample", party="DR", number=3, term_start="1801-03-04",
                  term_end="1809-03-04", score_public_mandate=70, score_effectiveness=60, score_historical_legacy=80),
        President(id="placeholder-99", name="Pat Placeholder", party="R", number=99, term_start="2025-01-20",
                  is_current=True, score_public_mandate=55, score_effectiveness=50),
        Justice(id="jane_q_placeholder", name="Jane Q. Placeholder", last_name="Placeholder",
                date_start="2020-10-27", is_active=True),
        JusticeVote(justice_id="jane_q_placeholder", case_id="2024-001", case_name="Smoke v. Fire", case_term="2024",
                    decided_date="2025-06-01", vote="majority", opinion_type="majority", majority_votes=6,
                    minority_votes=3),
        Nomination(congress=119, citation="PN12", number=12, description="Jane Q. Doe, of Texas, to be a Member",
                   received_date="2026-01-05", organization="Placeholder Board", position_title="Member",
                   nominee_count=1, nominees=json.dumps([{"names": [{"firstName": "Jane", "lastName": "Doe",
                                                                      "state": "TX"}]}]),
                   actions=json.dumps([{"date": "2026-02-01", "code": "S05120", "type": "Floor",
                                        "text": "Confirmed by the Senate by Yea-Nay Vote. 1 - 0."}]),
                   latest_action_date="2026-02-01", latest_action_text="Confirmed", status="confirmed",
                   status_date="2026-02-01", committee_code="SSGA00", committee_name="Placeholder Committee"),
        Agency(slug="placeholder-department", fr_id=1, name="Placeholder Department", synced_at=datetime(2026, 10, 1)),
        Agency(slug="placeholder-bureau", fr_id=2, name="Placeholder Bureau", parent_slug="placeholder-department",
               synced_at=datetime(2026, 10, 1)),
        ExploreDocument(doc_type="Proposed Rule", source="Federal Register", title="Placeholder rule",
                        summary="A rule.", body="A rule.", date="2026-09-01", chamber="Regulatory",
                        external_id="2026-00001", agency_slug="placeholder-bureau", agency_name="Placeholder Bureau",
                        comments_close_on="2099-01-01",
                        comment_url="http://www.regulations.gov/commenton/PLACEHOLDER-2026-0001-0001"),
    ])
    db_session.commit()
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _body(client, path, **params):
    resp = client.get(f"/api/public/v1{path}", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("path,params,schema", [
    ("/", {}, PublicApiIndexSchema),
    ("/senators", {}, PublicSenatorPageSchema),
    ("/senators/S000001", {}, PublicSenatorProfileSchema),
    ("/senators/S000001/history", {}, PublicHistorySchema),
    ("/representatives", {}, PublicRepresentativePageSchema),
    ("/representatives", {"state": "GA"}, PublicRepresentativePageSchema),
    ("/representatives/R000001", {}, PublicRepresentativeProfileSchema),
    ("/representatives/R000001/history", {}, PublicHistorySchema),
    ("/senators/S000001/breakdown", {}, PublicScoreBreakdownSchema),
    ("/representatives/R000001/breakdown", {}, PublicScoreBreakdownSchema),
    ("/senators/S000001/votes", {}, PublicMemberVotesSchema),
    ("/representatives/R000001/votes", {}, PublicMemberVotesSchema),
    ("/bills/HR.1/votes", {}, PublicBillVotesSchema),
    ("/search", {"q": "wildfire"}, PublicSearchResponseSchema),
    ("/presidential-support/senate", {}, PublicPresidentialSupportSchema),
    ("/district-lean", {}, PublicDistrictLeanSchema),
    ("/roll-calls/senate/119/1/5", {}, PublicRollCallDetailSchema),
    ("/roll-calls", {"chamber": "senate"}, PublicRollCallPageSchema),
    ("/presidents", {}, PublicPresidentListSchema),
    ("/presidents/example-2", {}, PublicPresidentSchema),
    ("/justices", {}, PublicJusticeListSchema),
    ("/justices/jane_q_placeholder", {}, PublicJusticeSchema),
    ("/justices/jane_q_placeholder/cases", {}, PublicJusticeCasesSchema),
    ("/nominations", {}, PublicNominationPageSchema),
    ("/nominations/119/PN12", {}, PublicNominationDetailSchema),
    ("/agencies", {}, PublicAgencyListSchema),
    ("/agencies/placeholder-department", {}, PublicAgencyDetailSchema),
    ("/comment-periods", {}, PublicCommentPeriodPageSchema),
])
def test_every_response_matches_its_documented_schema(client, path, params, schema):
    body = _body(client, path, **params)
    schema.model_validate(body)
    # Non-empty, or a list-of-anything schema would pass vacuously.
    assert body.get("entries") or body.get("snapshots") or body.get("results") or "entries" not in body


def test_a_vote_carries_its_roll_call_and_each_party_split(client):
    (vote,) = _body(client, "/senators/S000001/votes")["entries"]
    assert vote["votedWithParty"] is True and vote["rollCall"]["number"] == 5
    assert (vote["partyLeaning"], vote["policyArea"]) == ("D", "PROCEDURAL")
    assert vote["rollCall"]["parties"] == [{"party": "R", "yea": 0, "nay": 3, "present": 0, "notVoting": 0},
                                           {"party": "D", "yea": 3, "nay": 0, "present": 0, "notVoting": 0}]
    assert vote["rollCall"]["sourceUrl"].startswith("https://www.senate.gov/")
    (rc,) = _body(client, "/bills/hr.1/votes")["entries"]
    assert (rc["yeas"], rc["nays"], rc["chamber"]) == (1, 0, "senate")
    assert _body(client, "/bills/HR.1/votes")["covered"] is True


def test_a_congress_whose_roll_calls_are_not_held_says_so(client, db_session):
    """An empty list outside the held Congresses is "not held", not "no
    recorded vote": a past Congress's bill the House passed on a roll call
    read as a bill with none."""
    held = sorted({rc.congress for rc in db_session.query(RollCall)})
    body = _body(client, "/bills/HR.1/votes", congress=held[0] - 1)
    PublicBillVotesSchema.model_validate(body)
    assert (body["entries"], body["covered"], body["heldCongresses"]) == ([], False, held)


@pytest.mark.parametrize("path,member,schema", [
    ("/senators/old-s", "S000001", PublicSenatorProfileSchema),
    ("/senators/old-s/history", "S000001", PublicHistorySchema),
    ("/representatives/old-r", "R000001", PublicRepresentativeProfileSchema),
    ("/representatives/old-r/history", "R000001", PublicHistorySchema),
    ("/senators/old-s/breakdown", "S000001", PublicScoreBreakdownSchema),
])
def test_a_renamed_id_answers_under_the_current_one(client, db_session, path, member, schema):
    """As documented on the id parameter: an id a member had before a
    rename (app/member_ids.py) still works, and the body names the current id."""
    db_session.add_all([MemberIdAlias(old_id="old-s", new_id="S000001"),
                        MemberIdAlias(old_id="old-r", new_id="R000001")])
    db_session.commit()
    body = _body(client, path)
    schema.model_validate(body)
    assert body["id"] == member
    assert body.get("siteUrl", f"/politicians/{member}").endswith(f"/politicians/{member}")


def test_states_match_their_documented_schema(client):
    states = _body(client, "/states")
    assert states
    for state in states:
        PublicStateSchema.model_validate(state)


def test_state_counts_are_serving_members_only(client, db_session):
    """A departed member's row stays through the retirement grace period;
    counted, it gave a state with a newly seated successor three senators
    (live, 2026-10-08) while /senators listed two."""
    db_session.add_all([
        Senator(id="S000009", name="Old Doe", state="GA", party="D", is_current=False, **SCORES),
        Representative(id="R000009", name="Old Roe", state="GA", district=6, party="R", is_current=False, **SCORES),
    ])
    db_session.commit()
    ga = next(s for s in _body(client, "/states") if s["code"] == "GA")
    assert (ga["senatorCount"], ga["representativeCount"]) == (1, 1)


@pytest.mark.parametrize("chamber,member,state", [("senators", "S000001", "GA"), ("representatives", "R000001", "GA")])
def test_rank_is_chamber_wide_whatever_the_filters(client, chamber, member, state):
    """Both lists rank the same way: second of two nationally stays second
    when the state filter leaves it alone on the page."""
    everyone = _body(client, f"/{chamber}")["entries"]
    assert [e["rank"] for e in everyone] == [1, 2] and everyone[1]["id"] == member
    (only,) = _body(client, f"/{chamber}", state=state.lower())["entries"]
    assert (only["id"], only["rank"]) == (member, 2)
    assert only["siteUrl"].endswith(f"/politicians/{member}")


@pytest.mark.parametrize("model,chamber,extra", [(Senator, "senators", {}),
                                                 (Representative, "representatives", {"district": 1})])
def test_rank_is_the_one_the_site_shows(client, db_session, model, chamber, extra):
    """Ranked as displayed: two members whose scores round to the same
    whole number share a rank, the higher unrounded score listed first.
    The Senate list ranked on the unrounded score (63 of 100 senators off
    their site rank, 2026-10-11); the House list ordered a tie by name."""
    db_session.add_all([
        model(id="X000001", name="Aaa Placeholder", state="TX", party="R", **extra,
              **{**SCORES, "score_funding_independence": 95}),
        model(id="X000002", name="Zzz Placeholder", state="TX", party="R", **extra,
              **{**SCORES, "score_funding_independence": 95.5}),
    ])
    db_session.commit()
    rows = _body(client, f"/{chamber}")["entries"]
    overall = [r["representationScore"]["overall"] for r in rows]
    assert overall == sorted(overall, reverse=True)
    assert [(r["id"], r["rank"]) for r in rows[:2]] == [("X000002", 1), ("X000001", 1)]
    assert round(overall[0]) == round(overall[1]) and overall[0] > overall[1]


@pytest.mark.parametrize("close,open_", [("2026-10-13", True), ("2026-10-11", True), ("2026-10-10", False), ("", False)])
def test_comment_url_only_while_the_period_is_open(client, db_session, monkeypatch, close, open_):
    """As documented: where to comment while the period is open, judged on
    the Eastern date (time_utils.comment_period_today)."""
    monkeypatch.setattr(public, "comment_period_today", lambda: "2026-10-11")
    db_session.add(ExploreDocument(
        doc_type="Proposed Rule", source="Federal Register", title="Smoke testing rule", summary="Smoke tests.",
        body="Smoke testing.", date="2026-09-01", chamber="Regulatory", comments_close_on=close or None,
        comment_url="https://www.regulations.gov/commenton/PLACEHOLDER-2026-0001-0001",
    ))
    db_session.commit()
    ensure_lexical_index(db_session.get_bind())
    (result,) = [r for r in _body(client, "/search", q="smoke testing")["results"] if r["title"] == "Smoke testing rule"]
    assert result["commentsCloseOn"] == close
    assert bool(result["commentUrl"]) is open_


# A value for each path parameter, and the query a route can't answer without.
_PATH_VALUES = {"senator_id": "S000001", "rep_id": "R000001", "bill_id": "HR.1", "chamber": "senate",
                "congress": "119", "session": "1", "number": "5", "president_id": "example-2",
                "justice_id": "jane_q_placeholder", "citation": "PN12", "slug": "placeholder-department"}
_REQUIRED_QUERY = {"search": {"q": "wildfire"}, "roll-calls": {"chamber": "senate"}}


def test_every_endpoint_answers_with_the_rate_limit_headers(client, monkeypatch):
    monkeypatch.setattr(public, "_spec", None)
    for path in _body(client, "/openapi.json")["paths"]:
        filled = path
        for name, value in _PATH_VALUES.items():
            filled = filled.replace("{" + name + "}", value)
        resp = client.get(filled, params=_REQUIRED_QUERY.get(path.rsplit("/", 1)[-1]))
        assert resp.status_code == 200, path
        assert {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"} <= set(resp.headers), path


@pytest.mark.parametrize("path", ["/senators/nobody/history", "/representatives/nobody/history",
                                  "/senators/nobody", "/representatives/nobody",
                                  "/senators/nobody/votes", "/representatives/nobody/votes",
                                  "/senators/nobody/breakdown", "/representatives/nobody/breakdown",
                                  "/bills/NOT-A-BILL/votes", "/presidential-support/house",
                                  "/roll-calls/senate/119/1/999", "/presidents/nobody-1", "/justices/nobody",
                                  "/justices/nobody/cases", "/nominations/119/PN999", "/agencies/nobody",
                                  "/comment-periods?agency=nobody"])
def test_unknown_member_is_404_not_an_empty_record(client, path):
    assert client.get(f"/api/public/v1{path}").status_code == 404


def test_filter_values_are_validated_and_listed_in_the_spec(client, monkeypatch):
    assert client.get("/api/public/v1/senators", params={"party": "X"}).status_code == 422
    assert client.get("/api/public/v1/search", params={"q": "rule", "doc_type": "Tweet"}).status_code == 422
    monkeypatch.setattr(public, "_spec", None)
    params = {p["name"]: p for p in _body(client, "/openapi.json")["paths"]["/api/public/v1/search"]["get"]["parameters"]}
    enum = params["doc_type"]["schema"]["anyOf"][0]["enum"]
    assert "Executive Order" in enum and "senate" in params["chamber"]["schema"]["anyOf"][0]["enum"]


def test_index_lists_every_documented_endpoint(client, monkeypatch):
    monkeypatch.setattr(public, "_spec", None)
    paths = _body(client, "/openapi.json")["paths"]
    assert set(_body(client, "/")["endpoints"]) == {f"GET {p}" for p in paths}


def test_spec_documents_exactly_the_public_routes(client, monkeypatch):
    monkeypatch.setattr(public, "_spec", None)
    spec = _body(client, "/openapi.json")
    assert spec["info"]["title"] == "Civitas Public API"
    paths = set(spec["paths"])
    assert all(p.startswith("/api/public/v1") for p in paths)
    assert "/api/public/v1/openapi.json" not in paths
    assert {"/api/public/v1/senators", "/api/public/v1/search"} <= paths
    # Response bodies are documented, not just parameters.
    ok = spec["paths"]["/api/public/v1/senators/{senator_id}"]["get"]["responses"]["200"]
    assert ok["content"]["application/json"]["schema"]["$ref"].endswith("PublicSenatorProfileSchema")
    assert "siteUrl" in spec["components"]["schemas"]["PublicSenatorProfileSchema"]["properties"]


@pytest.mark.parametrize("chamber,member", [("senators", "S000001"), ("representatives", "R000001")])
def test_list_rows_and_profiles_carry_the_same_score_block(client, chamber, member):
    """List rows published "confidence": null for every member (live,
    2026-10-03), while the profile carried the grades."""
    row = next(e for e in _body(client, f"/{chamber}")["entries"] if e["id"] == member)
    profile = _body(client, f"/{chamber}/{member}")
    assert row["representationScore"]["confidence"] == {"fundingIndependence": "high"}
    assert profile["representationScore"]["confidence"] == row["representationScore"]["confidence"]


@pytest.mark.parametrize("chamber,member", [("senators", "S000001"), ("representatives", "R000001")])
def test_promise_persistence_is_published_as_not_measured(client, chamber, member):
    """Campaign-promise tracking was removed in 2026-07; the stored value
    since is a constant (55 for 97 of 100 senators, live 2026-10-03), so the
    API says null rather than publish it as a measurement."""
    row = next(e for e in _body(client, f"/{chamber}")["entries"] if e["id"] == member)
    assert row["representationScore"]["promisePersistence"] is None
    assert _body(client, f"/{chamber}/{member}")["representationScore"]["promisePersistence"] is None
    (snap,) = _body(client, f"/{chamber}/{member}/history")["snapshots"]
    assert snap["promisePersistence"] is None


class TestCallerMistakes:
    """A filter the endpoint doesn't take is refused, not ignored; a choice
    written another way than the documented one is read as it; an unknown
    member id is a 404, not an empty search; and every 422 records which
    parameter broke which rule (ApiRejectionCount)."""

    def test_an_unknown_parameter_is_refused(self, client):
        r = client.get("/api/public/v1/search", params={"q": "tax", "type": "speech"})
        assert r.status_code == 422
        assert r.json()["detail"][0]["loc"] == ["query", "type"]
        assert "doc_type" in r.json()["detail"][0]["msg"]

    def test_choices_are_read_whatever_their_case_or_separators(self):
        from app.api.public import _canonical

        types = ("Senate Floor Speech", "Executive Order")
        assert _canonical("doc_type", "senate-floor-speech", types) == "Senate Floor Speech"
        assert _canonical("doc_type", "executive_order", types) == "Executive Order"
        assert _canonical("chamber", "Senate", ("senate", "house")) == "senate"
        assert _canonical("party", "Republican", ("D", "R", "I")) == "R"
        assert _canonical("state", "georgia", ()) == "GA"
        assert _canonical("doc_type", "speech", types) == "speech"  # left for validation to refuse

    def test_an_unknown_politician_is_a_404(self, client):
        r = client.get("/api/public/v1/search", params={"q": "tax", "politician_id": "nobody-here"})
        assert r.status_code == 404

    def test_a_justice_id_from_a_result_filters_a_search(self, client, db_session):
        """Every opinion result names its author's id in politicianId;
        passed back as the filter it was refused as unknown (2026-10-11)."""
        db_session.add_all([  # the fixture's justice
            ExploreDocument(doc_type="Supreme Court Opinion", source="Supreme Court", title="Wildfire v. Smoke",
                            summary="Opinion of the Court.", body="Wildfire smoke opinion.", date="2026-06-01",
                            chamber="Judicial", politician_id="jane_q_placeholder",
                            politician_name="Jane Q. Placeholder"),
        ])
        db_session.commit()
        ensure_lexical_index(db_session.get_bind())
        body = _body(client, "/search", q="wildfire", politician_id="jane_q_placeholder")
        assert [r["politicianId"] for r in body["results"]] == ["jane_q_placeholder"]

    def test_a_422_records_the_parameter_and_rule(self, client, monkeypatch):
        from app.api import public

        recorded = []
        monkeypatch.setattr(public, "record_api_request", lambda *a, **k: recorded.append((a, k)))
        client.get("/api/public/v1/search", params={"q": "x"})
        assert recorded[-1] == (("search_documents", "http", 422), {"rejection": ("q", "string_too_short")})


@pytest.mark.parametrize("chamber,member,entity", [("senators", "S000001", "senator"),
                                                   ("representatives", "R000001", "representative")])
def test_profiles_and_history_name_the_scoring_version(client, db_session, chamber, member, entity):
    """A score is citable to its method: /breakdown named the version and
    date, the profile and /history (what most clients read) named neither."""
    db_session.add(ScoreSnapshot(entity_type=entity, entity_id=member, date="2026-10-01", overall_score=60,
                                 score_1=60, score_2=50, score_3=55, score_4=40, score_5=70,
                                 algorithm_version="v6.35"))
    db_session.commit()
    profile = _body(client, f"/{chamber}/{member}")
    assert (profile["algorithmVersion"], profile["scoredOn"]) == ("v6.35", "2026-10-01")
    breakdown = _body(client, f"/{chamber}/{member}/breakdown")
    assert (breakdown["algorithmVersion"], breakdown["scoredOn"]) == ("v6.35", "2026-10-01")
    old, new = _body(client, f"/{chamber}/{member}/history")["snapshots"]
    assert (old["date"], old["algorithmVersion"]) == ("2026-09-01", None)
    assert (new["date"], new["algorithmVersion"]) == ("2026-10-01", "v6.35")


def test_a_profile_with_no_snapshot_names_no_version(client):
    profile = _body(client, "/senators/S000002")
    assert (profile["algorithmVersion"], profile["scoredOn"]) == (None, None)


def test_votes_are_every_position_the_member_recorded(client, db_session):
    """Every position on the chamber's roll calls, not the profile's stored
    selection (key_votes held 202 of a senator's 915 positions on
    2026-10-16): total is the member's positions, and a roll call the
    scoring run stored no vote row for is there too, with its party split."""
    db_session.add_all([
        RollCall(id=50, chamber="senate", congress=119, session=1, number=6, date="2026-02-02",
                 question="On the Motion to Table", result="Motion to Table Agreed to", yeas=1, nays=0),
        RollCallPosition(roll_call_id=50, member_id="S001", last_name="Doe", party="D", state="GA", position="Yea"),
        RollCall(id=51, chamber="senate", congress=118, session=2, number=6, date="2024-02-02",
                 question="On Passage", result="Passed", yeas=1, nays=0),
        RollCallPosition(roll_call_id=51, member_id="S001", last_name="Doe", party="D", state="GA", position="Yea"),
    ])
    db_session.commit()
    body = _body(client, "/senators/S000001/votes")
    PublicMemberVotesSchema.model_validate(body)
    assert (body["total"], body["congress"], body["chamberRollCalls"]) == (2, 119, 3)
    newest = body["entries"][0]
    assert newest["rollCall"]["number"] == 6 and newest["billId"] is None
    # A motion to table is housekeeping: no party signal, and no stored row.
    assert (newest["votedWithParty"], newest["partyLeaning"], newest["policyArea"]) == (None, None, None)
    assert newest["countedAsBreak"] is None  # no party-line record stored for this senator


def test_a_house_aye_against_the_party_is_the_scores_break(client):
    (vote,) = _body(client, "/representatives/R000001/votes")["entries"]
    assert (vote["vote"], vote["votedWithParty"], vote["countedAsBreak"]) == ("Yea", False, True)
    assert vote["billId"] == "HR.3" and vote["policyArea"] is None


def test_paging_through_every_position(client, db_session):
    db_session.add_all([
        RollCall(id=100 + n, chamber="senate", congress=119, session=2, number=100 + n, date=f"2026-03-{n:02d}",
                 question="On the Nomination PN1", result="Nomination Confirmed", yeas=1, nays=0)
        for n in range(1, 4)
    ] + [RollCallPosition(roll_call_id=100 + n, member_id="S001", last_name="Doe", party="D", state="GA",
                          position="Not Voting") for n in range(1, 4)])
    db_session.commit()
    first = _body(client, "/senators/S000001/votes", per_page=2)
    last = _body(client, "/senators/S000001/votes", per_page=2, page=2)
    assert (first["total"], first["totalPages"]) == (4, 2)
    seen = [e["rollCall"]["number"] for e in first["entries"] + last["entries"]]
    assert seen == [103, 102, 101, 5]
    assert first["entries"][0]["vote"] == "Not Voting"
def test_a_tie_the_vice_president_broke_is_stated(client, db_session):
    """A tie broken by the Vice President (who is no member, so the yeas and
    nays stay equal) carries the tie-breaker, as /developers documents."""
    db_session.add(RollCall(chamber="senate", congress=119, session=1, number=7, date="2026-02-03",
                            question="On Passage of the Bill", result="Bill Passed", yeas=50, nays=50,
                            bill_id="HR.2", tie_breaker_by="Vice President of the United States",
                            tie_breaker_vote="Yea"))
    db_session.commit()
    body = _body(client, "/bills/HR.2/votes")
    PublicBillVotesSchema.model_validate(body)
    (rc,) = body["entries"]
    assert (rc["yeas"], rc["nays"], rc["kind"], rc["candidates"]) == (50, 50, "question", [])
    assert rc["tieBreaker"] == {"byWhom": "Vice President of the United States", "vote": "Yea"}


def test_a_roll_call_lists_every_position_and_names_the_member(client):
    body = _body(client, "/roll-calls/senate/119/1/5")
    assert (body["yeas"], body["billId"], body["kind"]) == (1, "HR.1", "question")
    assert body["siteUrl"].endswith("/congress/votes/senate/119/1/5")
    assert len(body["positions"]) == 6
    doe = next(p for p in body["positions"] if p["lastName"] == "Doe")
    assert (doe["position"], doe["bucket"], doe["chamberMemberId"]) == ("Yea", "yea", "S001")
    # Every roll call the API gives carries its page.
    (vote,) = _body(client, "/senators/S000001/votes")["entries"]
    assert vote["rollCall"]["siteUrl"].endswith("/congress/votes/senate/119/1/5")


def test_roll_calls_are_listed_newest_first_and_a_congress_not_held_says_so(client):
    body = _body(client, "/roll-calls", chamber="senate")
    assert [e["number"] for e in body["entries"]] == [9, 5]
    assert body["total"] == 2 and body["covered"] is True
    past = _body(client, "/roll-calls", chamber="senate", congress=100)
    assert (past["entries"], past["covered"]) == ([], False)


def test_presidents_rank_the_former_and_list_a_sitting_one_unranked(client):
    entries = _body(client, "/presidents")["entries"]
    assert [(e["id"], e["rank"]) for e in entries] == [("sample-3", 1), ("example-2", 2), ("placeholder-99", None)]
    assert entries[0]["siteUrl"].endswith("/politicians/sample-3")
    assert _body(client, "/presidents/placeholder-99")["rank"] is None


def test_a_justices_cases_and_record(client):
    (row,) = _body(client, "/justices")["entries"]
    assert row["score"] == {"loyalty": None, "overall": None}
    cases = _body(client, "/justices/jane_q_placeholder/cases")
    (case,) = cases["entries"]
    assert (case["split"], case["vote"], cases["terms"]) == ("6-3", "majority", [2024])
    spec = _body(client, "/openapi.json")["paths"]["/api/public/v1/justices/{justice_id}/cases"]["get"]
    assert "CC BY-NC" in spec["description"]


def test_a_nomination_and_its_vote(client, db_session):
    db_session.add(RollCall(chamber="senate", congress=119, session=2, number=12, date="2026-02-01",
                            question="On the Nomination PN12", result="Nomination Confirmed", yeas=1, nays=0))
    db_session.commit()
    (row,) = _body(client, "/nominations")["entries"]
    assert (row["citation"], row["status"], row["nominees"]) == ("PN12", "confirmed", ["Jane Doe (TX)"])
    assert row["siteUrl"].endswith("/congress/nominations/119/PN12")
    detail = _body(client, "/nominations/119/PN12")
    assert detail["committee"] == {"code": "SSGA00", "name": "Placeholder Committee"}
    assert [rc["number"] for rc in detail["rollCalls"]] == [12]
    assert _body(client, "/nominations", status="pending")["entries"] == []


def test_a_nominations_list_says_when_it_is_only_part_of_the_congress(client):
    """Until the daily read has reached every nomination, the list is a
    sample: backfill.complete says so (never read: no marker yet)."""
    backfill = _body(client, "/nominations")["backfill"]
    assert backfill["complete"] is False and backfill["read"] == 1


def test_a_roll_call_of_a_congress_not_held_says_not_held(client):
    """Not held, with the window held, never "no roll call recorded"
    (the rule pages follow since #1064)."""
    resp = client.get("/api/public/v1/roll-calls/senate/100/1/5")
    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("Not held: Civitas holds roll calls from the 119th Congress on")
    missing = client.get("/api/public/v1/roll-calls/senate/119/1/999")
    assert missing.status_code == 404 and missing.json()["detail"].startswith("No roll call recorded")


def test_agencies_and_open_comment_periods(client, monkeypatch):
    monkeypatch.setattr(public, "comment_period_today", lambda: "2098-12-31")
    agencies = {a["slug"]: a for a in _body(client, "/agencies")["entries"]}
    assert agencies["placeholder-department"]["documents"] == 1  # its bureau's, counted under it
    detail = _body(client, "/agencies/placeholder-department")
    assert (detail["openCommentPeriods"], detail["children"]) == (1, [{"slug": "placeholder-bureau",
                                                                       "name": "Placeholder Bureau"}])
    (period,) = _body(client, "/comment-periods", agency="placeholder-department")["entries"]
    assert (period["daysLeft"], period["key"]) == (1, "2026-00001")
    assert period["commentUrl"].startswith("https://www.regulations.gov/")
    monkeypatch.setattr(public, "comment_period_today", lambda: "2099-01-02")
    assert _body(client, "/comment-periods")["entries"] == []


@pytest.mark.parametrize("path", ["/senators", "/senators/S000001", "/senators/S000001/breakdown",
                                  "/senators/S000001/history", "/presidents"])
def test_composite_responses_carry_deprecation_and_sunset(client, path):
    """The composite (overall, rank) is deprecated: headers say so, and the
    OpenAPI schema marks the fields."""
    res = client.get(f"/api/public/v1{path}")
    assert res.status_code == 200
    assert res.headers["Deprecation"].startswith("@")
    assert res.headers["Sunset"].endswith("GMT")
    assert 'rel="deprecation"' in res.headers["Link"]


def test_unrelated_responses_carry_no_deprecation(client):
    assert "Deprecation" not in client.get("/api/public/v1/states").headers


def test_composite_fields_are_marked_deprecated_in_the_schema():
    from app.schemas import PublicScoreBreakdownSchema, PublicSenatorRowSchema

    assert PublicScoreBreakdownSchema.model_json_schema()["properties"]["overall"]["deprecated"] is True
    assert PublicSenatorRowSchema.model_json_schema()["properties"]["rank"]["deprecated"] is True
