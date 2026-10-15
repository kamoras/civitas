"""A member's representation score can be reproduced from the public API.

/api/public/v1/{senators|representatives}/{id}/breakdown publishes each
dimension's components, weights and the reference values they were
compared against. These tests store scores the way the pipeline does
(score_calculator.calculate_scores over the member's stored inputs), then
recompute every dimension and the overall score from nothing but the API
response, by the rule the schema documents.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.database import get_db
from app.models import (
    Donor,
    IndustryDonation,
    KeyVote,
    RepDonor,
    RepIndustryDonation,
    RepKeyVote,
    Representative,
    RepSponsoredBill,
    ScoreSnapshot,
    Senator,
    SponsoredBill,
)
from app.pipeline.analyze import score_calculator
from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION, calculate_scores, compute_overall_score
from app.schemas import PublicScoreBreakdownSchema
from app.services._scorecard_common import build_score_breakdown_entity
from app.services.representative_service import _rep_eager_options
from app.services.senator_service import _senator_eager_options

# A survey reading for the fixture senator, so Constituent Approval (the one
# "points" component) is measured: no fixture name is in the real survey.
_APPROVAL = {"index": 0.1, "z": 0.8, "groups": [("R", 0.2, 0.15), ("I", 0.5, 0.45)],
             "survey": "Placeholder survey", "fielded": 2024}

_RECORD = {"congress": 119, "votes": 60, "breaks": [{"rollCall": "senate-119-1-1", "vote": "Nay"}] * 4,
           "flankBreaks": []}


def _funding_rows(donor_model, industry_model, fk: str) -> list:
    rows = [donor_model(**{fk: "x"}, name=f"Donor {i} PAC", total=40_000 - i * 3_000, type="PAC", rank=i + 1)
            for i in range(12)]
    rows += [industry_model(**{fk: "x"}, industry=code, name=code.title(), total=total, percentage=0)
             for code, total in (("TECH", 300_000), ("PHARMA", 120_000), ("ENERGY", 60_000))]
    return rows


def _bills(model, fk: str, n: int) -> list:
    return [model(**{fk: "x"}, bill_id=f"HR.{100 + i}", title=f"Placeholder Act {i}", bill_type="hr" if fk ==
                  "representative_id" else "s", congress=119, is_law=i == 0,
                  stage="ENACTED" if i == 0 else ("PASSED_CHAMBER" if i < 3 else "INTRODUCED"))
            for i in range(n)]


def _store_like_the_pipeline(db, member, eager, donation_attr: str, entity_type: str) -> None:
    """Score the member's stored inputs with the pipeline's own function and
    store the result as a run would, with the day's history snapshot."""
    db.flush()
    loaded = db.query(type(member)).options(*eager()).filter_by(id=member.id).one()
    scores = calculate_scores(build_score_breakdown_entity(loaded, lobbying_donation_attr=donation_attr))
    loaded.score_funding_independence = scores["fundingIndependence"]
    loaded.score_constituent_alignment = scores["constituentAlignment"]
    loaded.score_funding_diversity = scores["fundingDiversity"]
    loaded.score_legislative_effectiveness = scores["legislativeEffectiveness"]
    db.add(ScoreSnapshot(entity_type=entity_type, entity_id=member.id, date="2026-10-14",
                         overall_score=compute_overall_score(loaded), algorithm_version=ALGORITHM_VERSION,
                         score_1=loaded.score_funding_independence, score_3=loaded.score_constituent_alignment,
                         score_4=loaded.score_funding_diversity, score_5=loaded.score_legislative_effectiveness))
    db.commit()


def _senator(db, member_id="s-full", bills=6):
    member = Senator(id=member_id, bioguide_id="Z000001", name="Pat Placeholder", state="OH", party="D",
                   years_in_office=8, total_raised=4_000_000, total_contributions=4_000_000,
                   total_from_pacs=600_000, small_donor_percentage=31, leadership_score=0.6,
                   attracted_bipartisanship_score=0.4, party_line_record=json.dumps(_RECORD))
    db.add(member)
    for row in _funding_rows(Donor, IndustryDonation, "senator_id") + _bills(SponsoredBill, "senator_id", bills):
        row.senator_id = member_id
        db.add(row)
    db.add(KeyVote(senator_id=member_id, bill_name="Placeholder Act", bill_id="S.1", date="2026-02-01",
                   vote="Yea", voted_with_party=True, vote_category="key"))
    _store_like_the_pipeline(db, member, _senator_eager_options,
                             "donation_to_senator", "senator")


def _representative(db, member_id="r-full", bills=8):
    member = Representative(id=member_id, bioguide_id="Z000002", name="Lee Placeholder", state="OH", district=3,
                          party="R", years_in_office=4, total_raised=1_500_000, total_contributions=1_500_000,
                          total_from_pacs=700_000, small_donor_percentage=9, leadership_score=0.3,
                          attracted_bipartisanship_score=0.2, party_line_record=json.dumps(
                              {**_RECORD, "breaks": [{"rollCall": "house-119-1-1", "vote": "Yea"}]}))
    db.add(member)
    for row in _funding_rows(RepDonor, RepIndustryDonation, "representative_id") + _bills(
            RepSponsoredBill, "representative_id", bills):
        row.representative_id = member_id
        db.add(row)
    db.add(RepKeyVote(representative_id=member_id, bill_name="Placeholder Act", bill_id="HR.1", date="2026-02-01",
                      vote="Yea", voted_with_party=True, vote_category="key"))
    _store_like_the_pipeline(db, member, _rep_eager_options,
                             "donation_to_representative", "representative")


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(score_calculator, "senate_approval", lambda *a, **k: _APPROVAL)
    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _breakdown(client, chamber: str, member_id: str) -> dict:
    resp = client.get(f"/api/public/v1/{chamber}/{member_id}/breakdown")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    PublicScoreBreakdownSchema.model_validate(body)
    return body


def recompute(dimension: dict) -> float:
    """The documented rule, from the published components alone (unclamped,
    unrounded)."""
    measured = [c for c in dimension["components"] if c["score"] is not None]
    if not measured:
        return 50.0
    mean = [c for c in measured if c["combines"] == "mean"]
    points = [c for c in measured if c["combines"] == "points"]
    return (sum(c["weight"] * c["score"] for c in mean) / sum(c["weight"] for c in mean)
            + sum(c["weight"] * (c["score"] - 50) for c in points))


def _assert_reproduced(body: dict) -> None:
    for key, dim in body["dimensions"].items():
        assert dim["score"] == dim["storedScore"], key
        value = recompute(dim)
        # Components are published to one decimal: within 0.1 of a value
        # that rounds to the score, as the schema states.
        low, high = dim["score"] - 0.5, dim["score"] + 0.5
        if dim["score"] == 0:
            low = float("-inf")
        if dim["score"] == 100:
            high = float("inf")
        assert low - 0.1 <= value <= high + 0.1, (key, value, dim["score"])
    weighted = sum(body["weights"][k] * body["dimensions"][k]["storedScore"] for k in body["weights"])
    assert round(weighted, 2) == body["overall"]
    assert abs(sum(body["weights"].values()) - 1) < 1e-9


@pytest.mark.parametrize("chamber,build,member_id", [("senators", _senator, "s-full"),
                                                     ("representatives", _representative, "r-full")])
def test_stored_scores_recompute_from_the_response(client, db_session, chamber, build, member_id):
    build(db_session)
    body = _breakdown(client, chamber, member_id)
    _assert_reproduced(body)
    # Every scored dimension was really measured here, not a neutral 50.
    for key in ("fundingIndependence", "constituentAlignment", "legislativeEffectiveness"):
        assert body["dimensions"][key]["components"], key
    assert (body["algorithmVersion"], body["scoredOn"], body["recomputedWith"]) == (
        ALGORITHM_VERSION, "2026-10-14", ALGORITHM_VERSION)
    refs = body["references"]
    assert refs["funding"] and refs["constituent"] and refs["legislative"]
    assert refs["seat"]["state"] == "OH"


def test_the_points_component_is_what_the_rule_says(client, db_session):
    """Constituent approval adds weight x (score - 50): read as a mean
    component instead, the senator's alignment would not recompute."""
    _senator(db_session)
    dim = _breakdown(client, "senators", "s-full")["dimensions"]["constituentAlignment"]
    (approval,) = [c for c in dim["components"] if c["combines"] == "points"]
    assert approval["label"] == "Constituent approval" and approval["score"] is not None
    as_mean = recompute({"components": [{**c, "combines": "mean"} for c in dim["components"]]})
    assert round(as_mean) != dim["score"]


@pytest.mark.parametrize("chamber", ["senators", "representatives"])
def test_unknown_member_is_404(client, chamber):
    assert client.get(f"/api/public/v1/{chamber}/nobody/breakdown").status_code == 404
