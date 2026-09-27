"""Startup rescore of Constituent Alignment onto the current reference."""

import json

from sqlalchemy.orm import sessionmaker

from app.models import KeyVote, PipelineRun, PipelineStatus, Senator
from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
from app.pipeline.analyze.score_calculator import CONSTITUENT_REFERENCE_STATISTIC, _constituent_alignment_core
from app.pipeline.constituent_rescore import rescore_stale_constituent_alignment
from app.pipeline.transform.normalize_votes import stored_vote

_STATES = ["CA", "TX", "OH", "PA", "WY", "VT", "GA", "AZ", "NY", "FL"]


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False)


def _seed(db_session, n=40):
    for i in range(n):
        s = Senator(
            id=f"S{i:03d}", bioguide_id=f"S{i:03d}", name=f"Senator {i}", state=_STATES[i % 10],
            party="R" if i % 2 else "D", is_current=True,
            score_constituent_alignment=100.0, score_confidence=json.dumps({"constituentAlignment": "high"}),
        )
        db_session.add(s)
        for j in range(30):
            db_session.add(KeyVote(
                senator_id=s.id, bill_name=f"Bill {j}", bill_id=f"b-{j}", date="2025-03-01",
                vote="Yea", voted_with_party=j >= (i % 7),
            ))
    db_session.commit()


def _stale_entry():
    entry = dict(CONSTITUENT_REFERENCE.load()["senate"])
    entry.pop("statistic", None)
    return entry


def test_stale_reference_is_remeasured_and_scores_rescored(db_session):
    CONSTITUENT_REFERENCE.write("senate", _stale_entry())
    _seed(db_session)

    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]

    ref = CONSTITUENT_REFERENCE.load()["senate"]
    assert ref["statistic"] == CONSTITUENT_REFERENCE_STATISTIC and ref["n"] == 40
    db_session.expire_all()
    for s in db_session.query(Senator):
        votes = [stored_vote(v.id, v.bill_id, v.voted_with_party)
                 for v in db_session.query(KeyVote).filter(KeyVote.senator_id == s.id)]
        core = _constituent_alignment_core(
            {"keyVotes": votes, "recentVotes": [], "effectiveParty": s.party}, [], {},
            s.state, s.party, bioguide_id=s.bioguide_id, reference={"senate": ref},
        )
        assert s.score_constituent_alignment == core["score"]
        confidence = json.loads(s.score_confidence)
        # The grade is kept; the vote-part status is added from the same branch.
        assert confidence == {"constituentAlignment": "high", "constituentAlignmentVotePart": core["vote_part_status"]}
    assert len({s.score_constituent_alignment for s in db_session.query(Senator)}) > 1


def test_current_or_absent_reference_is_left_alone(db_session):
    _seed(db_session)
    # No persisted entry: scored against the bundled prior, as the breakdown reads.
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    CONSTITUENT_REFERENCE.write("senate", CONSTITUENT_REFERENCE.load()["senate"])
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    db_session.expire_all()
    assert {s.score_constituent_alignment for s in db_session.query(Senator)} == {100.0}


def test_skipped_while_a_pipeline_run_is_in_progress(db_session):
    CONSTITUENT_REFERENCE.write("senate", _stale_entry())
    _seed(db_session)
    db_session.add(PipelineRun(status=PipelineStatus.RUNNING))
    db_session.commit()
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []


def test_too_few_members_to_measure_leaves_scores(db_session):
    CONSTITUENT_REFERENCE.write("senate", _stale_entry())
    _seed(db_session, n=4)
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    db_session.expire_all()
    assert {s.score_constituent_alignment for s in db_session.query(Senator)} == {100.0}


def test_house_is_rescored_by_district(db_session):
    from app.models import Representative, RepKeyVote

    entry = dict(CONSTITUENT_REFERENCE.load()["house"])
    entry.pop("statistic", None)
    CONSTITUENT_REFERENCE.write("house", entry)
    for i in range(40):
        r = Representative(
            id=f"H{i:03d}", bioguide_id=f"H{i:03d}", name=f"Rep {i}", state=_STATES[i % 10],
            district=i % 5, party="R" if i % 2 else "D", is_current=True,
            score_constituent_alignment=100.0,
        )
        db_session.add(r)
        for j in range(30):
            db_session.add(RepKeyVote(
                representative_id=r.id, bill_name=f"Bill {j}", bill_id=f"b-{j}", date="2025-03-01",
                vote="Yea", voted_with_party=j >= (i % 7),
            ))
    db_session.commit()

    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["house"]
    ref = CONSTITUENT_REFERENCE.load()["house"]
    assert ref["statistic"] == CONSTITUENT_REFERENCE_STATISTIC
    db_session.expire_all()
    assert len({r.score_constituent_alignment for r in db_session.query(Representative)}) > 1
