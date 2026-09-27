"""Commemorative bill detection and its weight in Legislative Effectiveness."""

import numpy as np
import pytest

from app.pipeline.analyze import commemorative as cm
from app.pipeline.analyze.score_calculator import _les_significance_weight, _les_stage_counts


class _FakeModel:
    """Maps each text to a unit vector: prototypes and titles containing
    'post office' share an axis; everything else sits on another."""

    def encode(self, texts, **_):
        out = []
        for t in texts:
            v = np.array([1.0, 0.0]) if any(w in t.lower() for w in ("post office", "gold medal", "commemorat", "designates")) \
                else np.array([0.0, 1.0])
            out.append(v)
        return np.array(out)


def test_calibration_matches_the_prototypes():
    """The threshold was fitted to these exact prototypes; editing them
    without rerunning scripts/calibrate_commemorative.py fails here."""
    assert cm.calibration()["prototype_hash"] == cm.prototype_hash()
    assert 0.0 < cm.calibration()["threshold"] < 1.0


def test_classifies_by_margin_over_threshold(monkeypatch):
    monkeypatch.setattr(cm, "_prototype_cache", None)
    flags = cm.classify_commemorative(
        ["To designate the facility of the United States Postal Service as the Jane Doe Post Office",
         "To amend the Internal Revenue Code to extend a credit"],
        model=_FakeModel(),
    )
    assert flags == [True, False]


def test_mark_commemorative_is_best_effort(monkeypatch):
    bills = [{"title": "A bill"}]

    def boom(*_a, **_k):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(cm, "classify_commemorative", boom)
    cm.mark_commemorative(bills)
    assert "commemorative" not in bills[0]  # left unflagged: weighted as substantive


def test_commemorative_bills_weigh_like_resolutions():
    assert _les_significance_weight("hr") == 5.0
    assert _les_significance_weight("hr", commemorative=True) == 1.0
    law = {"billType": "hr", "isLaw": True, "commemorative": True}
    assert _les_stage_counts([law]) == [1.0, 1.0, 1.0, 1.0]


def _rep(bills, **extra):
    return {"id": "R-X1", "bioguideId": "X000001", "name": "Rep X", "state": "CT", "district": 1,
            "party": "D", "sponsoredBills": bills, **extra}


def test_flag_is_stored_with_the_bill(db_session):
    from app.models import RepSponsoredBill
    from app.services.representative_service import upsert_representative

    upsert_representative(db_session, _rep([
        {"billId": "HR.1", "title": "Post office naming", "billType": "hr", "commemorative": True},
        {"billId": "HR.2", "title": "Tax credit", "billType": "hr"},
    ]))
    db_session.commit()
    stored = {b.bill_id: b.commemorative for b in db_session.query(RepSponsoredBill)}
    assert stored == {"HR.1": True, "HR.2": False}


def test_unavailable_list_keeps_stored_bills(db_session):
    """A failed fetch sends an empty, unavailable list: the stored bills stay."""
    from app.models import RepSponsoredBill
    from app.services.representative_service import upsert_representative

    upsert_representative(db_session, _rep([{"billId": "HR.1", "title": "A bill", "billType": "hr"}]))
    db_session.commit()
    upsert_representative(db_session, _rep([], sponsoredBillsUnavailable=True))
    db_session.commit()
    assert [b.bill_id for b in db_session.query(RepSponsoredBill)] == ["HR.1"]


def test_senate_unavailable_list_keeps_stored_bills_and_flags(db_session):
    from app.models import SponsoredBill
    from app.pipeline.senate_pipeline import upsert_senator

    base = {"id": "S-X1", "bioguideId": "X000002", "name": "Sen X", "state": "CT", "party": "D"}
    upsert_senator(db_session, {**base, "sponsoredBills": [
        {"billId": "S.1", "title": "Gold Medal", "billType": "s", "commemorative": True},
    ]})
    db_session.commit()
    upsert_senator(db_session, {**base, "sponsoredBills": [], "sponsoredBillsUnavailable": True})
    db_session.commit()
    assert [(b.bill_id, b.commemorative) for b in db_session.query(SponsoredBill)] == [("S.1", True)]


@pytest.mark.slow
def test_real_similarity_model_separates_known_titles():
    """The shipped threshold on the production similarity model."""
    titles = [
        "To designate the facility of the United States Postal Service located at 103 Main Street "
        "in Springfield, Illinois, as the \"Jane Doe Post Office\".",
        "To award a Congressional Gold Medal, collectively, to the First Rhode Island Regiment.",
        "To amend the Internal Revenue Code of 1986 to extend the credit for electricity produced "
        "from certain renewable resources.",
        "To authorize appropriations for the Coast Guard for fiscal years 2025 and 2026.",
    ]
    assert cm.classify_commemorative(titles) == [True, True, False, False]
