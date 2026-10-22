"""A score snapshot keeps the four measures' figures on the day
(models.ScoreSnapshot.facts, filled from the member's stored breakdown)."""

import json

from app.measures import BY_KEY, snapshot_facts
from app.models import ScoreSnapshot, Senator
from app.pipeline.transform.normalize_votes import PARTY_SIDE_MAX, PARTY_SIDE_MIN, compute_party_vote_split

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


def test_the_breaks_method_states_the_rule_the_label_uses():
    """The party-line definition shown on the page is the code's own
    thresholds (normalize_votes.compute_party_vote_split), not "most of each
    party", which is CQ's party unity rule and not this one."""
    measured = BY_KEY["breaks"].measured
    assert f"at least {PARTY_SIDE_MIN:.0%} of one party" in measured
    assert f"at most {PARTY_SIDE_MAX:.0%} of the other" in measured
    assert "most Democrats and most Republicans" not in measured

    def roll(r_yea, d_yea):
        members = ([{"party": "R", "voteCast": "Yea"}] * r_yea + [{"party": "R", "voteCast": "Nay"}] * (10 - r_yea)
                   + [{"party": "D", "voteCast": "Yea"}] * d_yea + [{"party": "D", "voteCast": "Nay"}] * (10 - d_yea))
        return compute_party_vote_split({"members": members})["label"]

    # Majorities opposed (CQ's party unity vote) but short of the threshold.
    assert roll(6, 4) == "bipartisan"
    assert roll(7, 3) == "R"
