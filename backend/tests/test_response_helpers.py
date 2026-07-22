"""Tests for response_helpers.py — cached_json and score_history_json were
copy-pasted across six API router modules (senators, representatives,
presidents, justices, bills, politicians) before being consolidated here."""

import json

from app.api.response_helpers import PRESIDENT_DIMENSION_LABELS, cached_json, score_history_json
from app.models import ScoreSnapshot


def test_cached_json_sets_cache_control_header():
    resp = cached_json({"ok": True}, max_age=60)
    assert resp.headers["cache-control"] == "public, max-age=60, stale-while-revalidate=60"
    assert json.loads(resp.body) == {"ok": True}


def test_score_history_json_filters_by_entity_type_and_id(db_session):
    db_session.add(ScoreSnapshot(
        entity_type="senator", entity_id="S001", date="2026-07-01",
        overall_score=71.234, score_1=60, score_2=70, score_3=80, score_4=65, score_5=75,
        algorithm_version="v5.12",
    ))
    db_session.add(ScoreSnapshot(
        entity_type="representative", entity_id="S001", date="2026-07-01",
        overall_score=10.0, score_1=1, score_2=1, score_3=1, score_4=1, score_5=1,
    ))
    db_session.commit()

    resp = score_history_json(db_session, "senator", "S001")
    body = json.loads(resp.body)

    assert len(body["snapshots"]) == 1
    snap = body["snapshots"][0]
    assert snap["date"] == "2026-07-01"
    assert snap["overallScore"] == 71.2
    assert snap["algorithmVersion"] == "v5.12"
    assert snap["scores"] == {
        "fundingIndependence": 60.0,
        "promisePersistence": 70.0,
        "independentVoting": 80.0,
        "fundingDiversity": 65.0,
        "legislativeEffectiveness": 75.0,
    }


def test_score_history_json_uses_president_dimension_labels(db_session):
    db_session.add(ScoreSnapshot(
        entity_type="president", entity_id="test-prez", date="2026-07-01",
        overall_score=55.0, score_1=60, score_2=55, score_3=50, score_4=65, score_5=72,
        algorithm_version="v2",
    ))
    db_session.commit()

    resp = score_history_json(
        db_session, "president", "test-prez", dimension_labels=PRESIDENT_DIMENSION_LABELS,
    )
    body = json.loads(resp.body)

    assert len(body["snapshots"]) == 1
    assert body["snapshots"][0]["scores"] == {
        "publicMandate": 60.0,
        "effectiveness": 55.0,
        "competence": 50.0,
        "agencyAlignment": 65.0,
        "historicalLegacy": 72.0,
    }
