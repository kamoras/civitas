"""The public API's documentation is its contract (api/public.py).

Those routes return JSONResponse directly, which FastAPI never checks
against their response_model, so the published spec (/developers, from
/api/public/v1/openapi.json) could describe fields a response no longer
has, or miss ones it gained. This validates each endpoint's real body
against its documented schema; the Public* schemas forbid extra fields,
so drift in either direction fails here.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.api.router import api_router
from app.database import get_db
from app.models import ExploreDocument, Justice, MemberIdAlias, Representative, ScoreSnapshot, Senator
from app.pipeline.lexical_index import ensure_lexical_index
from app.schemas import (
    PublicApiIndexSchema,
    PublicHistorySchema,
    PublicRepresentativePageSchema,
    PublicRepresentativeProfileSchema,
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
        Representative(id="R000001", name="John Roe", state="GA", district=5, party="R", **SCORES),
        Representative(id="R000002", name="Kay Loe", state="WY", district=0, party="D",
                       **{**SCORES, "score_funding_independence": 95}),
        ScoreSnapshot(entity_type="senator", entity_id="S000001", date="2026-09-01",
                      overall_score=58.2, score_1=60, score_2=50, score_3=55, score_4=40, score_5=70),
        ScoreSnapshot(entity_type="representative", entity_id="R000001", date="2026-09-01",
                      overall_score=58.2, score_1=60, score_2=50, score_3=55, score_4=40, score_5=70),
        ExploreDocument(doc_type="Final Rule", source="Federal Register", title="Wildfire smoke rule",
                        summary="Standards for wildfire smoke.", body="Wildfire smoke exposure.",
                        date="2026-01-01", chamber="Regulatory"),
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
    ("/search", {"q": "wildfire"}, PublicSearchResponseSchema),
])
def test_every_response_matches_its_documented_schema(client, path, params, schema):
    body = _body(client, path, **params)
    schema.model_validate(body)
    # Non-empty, or a list-of-anything schema would pass vacuously.
    assert body.get("entries") or body.get("snapshots") or body.get("results") or "entries" not in body


@pytest.mark.parametrize("path,member,schema", [
    ("/senators/old-s", "S000001", PublicSenatorProfileSchema),
    ("/senators/old-s/history", "S000001", PublicHistorySchema),
    ("/representatives/old-r", "R000001", PublicRepresentativeProfileSchema),
    ("/representatives/old-r/history", "R000001", PublicHistorySchema),
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


def test_every_endpoint_answers_with_the_rate_limit_headers(client, monkeypatch):
    monkeypatch.setattr(public, "_spec", None)
    for path in _body(client, "/openapi.json")["paths"]:
        resp = client.get(path.replace("{senator_id}", "S000001").replace("{rep_id}", "R000001"),
                          params={"q": "wildfire"} if path.endswith("/search") else None)
        assert resp.status_code == 200, path
        assert {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"} <= set(resp.headers), path


@pytest.mark.parametrize("path", ["/senators/nobody/history", "/representatives/nobody/history",
                                  "/senators/nobody", "/representatives/nobody"])
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
        db_session.add_all([
            Justice(id="jane_q_placeholder", name="Jane Q. Placeholder", last_name="Placeholder"),
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
