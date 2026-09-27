"""Stored partisan-depth profiles are relabelled per chamber — the House too."""

import json

from app.models import Representative
from app.pipeline.analyze.party_platform import analyze_partisan_depth
from app.pipeline.partisan_depth_store import finalize_stored_partisan_depth


def _votes(n_with, n_against, leaning):
    # House votes carry a policy area and the bill's party leaning, as
    # house_pipeline's voting record does.
    areas = ["HEALTHCARE", "TAXES", "DEFENSE", "ENVIRONMENT"]
    yea = [{"vote": "Yea", "partyLeaning": leaning, "policyArea": areas[i % 4]} for i in range(n_with)]
    nay = [{"vote": "Nay", "partyLeaning": leaning, "policyArea": areas[i % 4]} for i in range(n_against)]
    return yea + nay


def test_a_profile_is_built_from_votes_alone():
    profile = analyze_partisan_depth([], "D", voting_record={"keyVotes": _votes(18, 2, "D")}, ideology_score=0.2)
    assert profile["totalPositions"] > 0 and "voteLean" in profile


def test_house_profiles_get_party_relative_depth(db_session):
    for i in range(30):
        party = "D" if i % 2 else "R"
        lean = party
        record = {"keyVotes": _votes(20 - i // 2, i // 2, lean)}
        profile = analyze_partisan_depth([], party, voting_record=record, ideology_score=0.1 if party == "D" else 0.9)
        db_session.add(Representative(
            id=f"R{i}", bioguide_id=f"R{i}", name=f"Rep {i}", state="CT", district=1,
            party=party, is_current=True, partisan_depth=json.dumps(profile),
        ))
    db_session.commit()

    finalize_stored_partisan_depth(db_session, Representative)

    depths = [json.loads(r.partisan_depth)["depth"] for r in db_session.query(Representative)]
    # Relabelled into within-party terciles: each label appears.
    assert {"deep", "moderate", "centrist"} <= set(depths)
