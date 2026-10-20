"""A score snapshot keeps the four measures' figures on the day
(models.ScoreSnapshot.facts, filled from the member's stored breakdown)."""

import json

from app.measures import snapshot_facts
from app.models import ScoreSnapshot, Senator

BREAKDOWN = {"scoredAt": "2026-10-19T03:00Z", "dimensions": {
    "fundingIndependence": {"facts": {"contributions": 100, "smallDonorShare": 0.13, "pacShare": 0.26}},
    "constituentAlignment": {"facts": {"partyVotes": 493, "breaks": 0, "breakRate": 0.0}},
    "legislativeEffectiveness": {"facts": {"bills": 89, "billsByStage": [76, 4, 7, 2, 0]}},
}}


def _snapshot(entity_type, entity_id):
    return ScoreSnapshot(entity_type=entity_type, entity_id=entity_id, date="2026-10-19", overall_score=60,
                         score_1=50, score_2=50, score_3=50, score_4=50, score_5=50)


def test_snapshot_keeps_the_members_measures(db_session):
    db_session.add(Senator(id="jane-doe", name="Jane Doe", state="OR", party="D",
                           score_breakdown=json.dumps(BREAKDOWN)))
    db_session.add(Senator(id="no-breakdown", name="No Breakdown", state="OR", party="D"))
    db_session.flush()
    db_session.add_all([_snapshot("senator", "jane-doe"), _snapshot("senator", "no-breakdown"),
                        _snapshot("president", "someone")])
    db_session.commit()
    facts = {s.entity_id: s.facts for s in db_session.query(ScoreSnapshot).all()}
    assert json.loads(facts["jane-doe"]) == {"smallGifts": 0.13, "pacMoney": 0.26, "breaks": 0.0, "billsMoved": 9,
                                            "partyVotes": 493, "breakCount": 0, "bills": 89}
    assert facts["no-breakdown"] is None and facts["someone"] is None


def test_snapshot_facts_none_without_figures():
    assert snapshot_facts(None) is None
    assert snapshot_facts({"fundingIndependence": {"facts": {}}}) is None
