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
from app.pipeline.analyze.score_calculator import (
    ALGORITHM_VERSION,
    LES_BILLS_STATUS,
    calculate_confidence,
    calculate_scores,
    compute_overall_score,
    explain_scores,
    load_les_reference,
)
from app.pipeline.sponsorship_backfill import carry_forward_sponsored_bills
from app.schemas import PublicScoreBreakdownSchema
from app.score_keeping import stored_breakdown
from app.services._scorecard_common import build_score_breakdown_entity
from app.services.representative_service import _rep_eager_options, upsert_representative
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


def _store_like_the_pipeline(db, member, eager, donation_attr: str, entity_type: str, keep: bool = True) -> None:
    """Score the member's stored inputs with the pipeline's own function and
    store the result as a run would, with the day's history snapshot and,
    unless `keep` is False (a score stored before they were kept), the
    run's breakdown."""
    db.flush()
    loaded = db.query(type(member)).options(*eager()).filter_by(id=member.id).one()
    inputs = build_score_breakdown_entity(loaded, lobbying_donation_attr=donation_attr)
    scores = calculate_scores(inputs)
    loaded.score_breakdown = stored_breakdown(explain_scores(inputs)) if keep else None
    loaded.score_funding_independence = scores["fundingIndependence"]
    loaded.score_constituent_alignment = scores["constituentAlignment"]
    loaded.score_funding_diversity = scores["fundingDiversity"]
    loaded.score_legislative_effectiveness = scores["legislativeEffectiveness"]
    db.add(ScoreSnapshot(entity_type=entity_type, entity_id=member.id, date="2026-10-14",
                         overall_score=compute_overall_score(loaded), algorithm_version=ALGORITHM_VERSION,
                         score_1=loaded.score_funding_independence, score_3=loaded.score_constituent_alignment,
                         score_4=loaded.score_funding_diversity, score_5=loaded.score_legislative_effectiveness))
    db.commit()


def _senator(db, member_id="s-full", bills=6, keep=True):
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
                             "donation_to_senator", "senator", keep)


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


def test_a_bill_that_moves_after_the_run_leaves_the_arithmetic_on_the_score(client, db_session):
    """2026-10-16: two senators' bills became law after the night's run, the
    hourly refresh stored it, and the breakdown recomputed on it showed
    components that gave 86 beside a score of 75. The run's own components
    are served, and today's records are reported beside them."""
    _senator(db_session)
    bill = db_session.query(SponsoredBill).filter_by(senator_id="s-full", stage="INTRODUCED").first()
    bill.stage, bill.is_law = "ENACTED", True
    db_session.commit()

    body = _breakdown(client, "senators", "s-full")
    _assert_reproduced(body)
    les = body["dimensions"]["legislativeEffectiveness"]
    assert les["basis"] == "scored"
    since = les["sinceScored"]
    assert since["score"] > les["storedScore"] and since["billsChanged"] == 1
    assert since["scoredAt"].endswith("Z")
    # A dimension the move didn't touch says nothing.
    assert body["dimensions"]["fundingIndependence"]["sinceScored"] is None
    # The profile's own breakdown route serves the same arithmetic.
    internal = client.get("/api/senators/s-full/score-breakdown").json()["legislativeEffectiveness"]
    assert internal["score"] == les["storedScore"] and internal["sinceScored"] == since


def test_a_score_stored_before_breakdowns_were_kept_is_recomputed(client, db_session):
    _senator(db_session, keep=False)
    body = _breakdown(client, "senators", "s-full")
    _assert_reproduced(body)
    assert {d["basis"] for d in body["dimensions"].values()} == {"recomputed"}
    assert all(d["sinceScored"] is None for d in body["dimensions"].values())


def test_the_sum_points_and_clamp_are_stated(client, db_session):
    """Constituent approval is shown as the points it adds, each dimension
    states its sum before rounding, and a sum below 0 says it was held at 0
    (one senator's Constituent Alignment summed to -2.49 and showed 0)."""
    _senator(db_session)
    dim = _breakdown(client, "senators", "s-full")["dimensions"]["constituentAlignment"]
    (approval,) = [c for c in dim["components"] if c["combines"] == "points"]
    assert approval["points"] == round(approval["weight"] * (approval["score"] - 50), 1)
    assert dim["sum"] == round(recompute(dim), 1) and round(dim["sum"]) == dim["score"]
    assert dim["clamped"] is False

    member = db_session.get(Senator, "s-full")
    member.score_constituent_alignment = 0
    member.score_breakdown = stored_breakdown({"constituentAlignment": {"score": 0, "components": [
        {"label": "Seat-relative vote alignment", "weight": 0.7, "score": 0.0, "detail": "d"},
        {"label": "Position congruence", "weight": 0.3, "score": 10.0, "detail": "d"},
        {"label": "Constituent approval", "weight": 0.3, "score": 30.0, "detail": "d"},
    ]}})
    db_session.commit()
    dim = _breakdown(client, "senators", "s-full")["dimensions"]["constituentAlignment"]
    assert dim["sum"] == -3.0 and dim["clamped"] is True and dim["score"] == dim["storedScore"] == 0


@pytest.mark.parametrize("chamber", ["senators", "representatives"])
def test_unknown_member_is_404(client, chamber):
    assert client.get(f"/api/public/v1/{chamber}/nobody/breakdown").status_code == 404


def _bill(n, stage="REFERRED", law=False):
    return {
        "billId": f"HR.{n}", "title": f"Act {n}", "introducedDate": "2025-03-01",
        "latestAction": "Referred to committee", "latestActionDate": "2025-03-01",
        "policyArea": "HEALTH", "policyAreas": [], "partyLeaning": None,
        "congress": 119, "billType": "hr", "isLaw": law, "stage": stage, "commemorative": False,
    }


def _member(rid, bio, **kw):
    return {
        "id": rid, "bioguideId": bio, "name": f"Member {rid}", "state": "TX", "district": 2,
        "party": "R", "yearsInOffice": 5, "leadershipScore": 0.6, "attractedBipartisanshipScore": 0.3,
        "funding": {}, "votingRecord": {"keyVotes": [], "recentVotes": []}, **kw,
    }


def test_fetched_carried_and_never_stored_bills_reproduce(client, db_session, pinned_les_reference):
    """The pipeline's sequence (carry bills forward, score, store) and the
    public breakdown recomputed from what was stored agree for each way a
    member's bills can arrive: read this run, carried from an earlier run
    that read them, or never read at all."""
    db_session.add(Representative(
        id="r-c", bioguide_id="C1", name="Member r-c", state="TX", district=2, party="R", years_in_office=5,
        score_confidence=json.dumps({LES_BILLS_STATUS: "fetched:2026-10-13"}),
    ))
    for b in (_bill(1, "ENACTED", True), _bill(2)):
        db_session.add(RepSponsoredBill(
            representative_id="r-c", bill_id=b["billId"], title=b["title"], congress=119, bill_type="hr",
            stage=b["stage"], is_law=b["isLaw"], latest_action=b["latestAction"],
        ))
    db_session.commit()
    members = [
        _member("r-f", "F1", sponsoredBills=[_bill(5, "PASSED_CHAMBER"), _bill(6)]),
        _member("r-c", "C1", sponsoredBills=[], sponsoredBillsUnavailable=True),
        _member("r-n", "N1"),  # never stored, never read
    ]
    carry_forward_sponsored_bills(db_session, Representative, members)
    reference = load_les_reference()
    for rep in members:
        scores = calculate_scores({**rep, "lesReference": reference})
        scores["confidence"] = calculate_confidence(rep)
        upsert_representative(db_session, {**rep, "representationScore": scores})
    db_session.commit()

    sources = {}
    for rid in ("r-f", "r-c", "r-n"):
        body = _breakdown(client, "representatives", rid)
        _assert_reproduced(body)
        sources[rid] = body["references"]["legislativeBills"].split(":")[0]
    assert sources == {"r-f": "fetched", "r-c": "carried", "r-n": "unavailable"}
