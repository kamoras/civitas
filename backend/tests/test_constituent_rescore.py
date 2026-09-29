"""Startup rescore of Constituent Alignment onto the current reference."""

import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import app.pipeline.analyze.score_calculator as sc
from app.models import (
    HousePipelineRun, KeyVote, PipelineRun, PipelineStatus, Representative, RepKeyVote, Senator,
)
from app.pipeline.analyze.population_reference import CONSTITUENT_REFERENCE
from app.pipeline.analyze.score_calculator import (
    CONSTITUENT_REFERENCE_STATISTIC,
    calculate_confidence,
    calculate_scores,
)
from app.pipeline.constituent_rescore import rescore_stale_constituent_alignment
from app.pipeline.transform.normalize_votes import stored_vote
from app.time_utils import utcnow

_STATES = ["CA", "TX", "OH", "PA", "WY", "VT", "GA", "AZ", "NY", "FL"]


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False)


def _party(i):
    return "R" if i % 2 else "D"


def _seed_senate(db_session, n=40, confidence='{"constituentAlignment": "high"}'):
    for i in range(n):
        s = Senator(
            id=f"S{i:03d}", bioguide_id=f"S{i:03d}", name=f"Senator {i}", state=_STATES[i % 10],
            party=_party(i), is_current=True,
            score_constituent_alignment=100.0, score_confidence=confidence,
        )
        db_session.add(s)
        for j in range(30):
            db_session.add(KeyVote(
                senator_id=s.id, bill_name=f"Bill {j}", bill_id=f"b-{j}", date="2025-03-01",
                vote="Yea", voted_with_party=j >= (i % 7),
            ))
    db_session.commit()


def _seed_house(db_session, n=40):
    for i in range(n):
        r = Representative(
            id=f"H{i:03d}", bioguide_id=f"H{i:03d}", name=f"Rep {i}", state=_STATES[i % 10],
            district=i % 5, party=_party(i), is_current=True, score_constituent_alignment=100.0,
        )
        db_session.add(r)
        for j in range(30):
            db_session.add(RepKeyVote(
                representative_id=r.id, bill_name=f"Bill {j}", bill_id=f"b-{j}", date="2025-03-01",
                vote="Yea", voted_with_party=j >= (i % 5),
            ))
    db_session.commit()


def _make_stale(chamber):
    entry = dict(CONSTITUENT_REFERENCE.load()[chamber])
    entry.pop("statistic", None)
    CONSTITUENT_REFERENCE.write(chamber, entry)


def _pipeline_scores(db_session, member, vote_model, fk_col, chamber):
    """What a pipeline run would store for this member: calculate_scores and
    calculate_confidence over a payload shaped like the pipelines build."""
    votes = [stored_vote(v.id, v.bill_id, v.voted_with_party)
             for v in db_session.query(vote_model).filter(fk_col == member.id)
             if v.voted_with_party is not None]
    payload = {
        "id": member.id, "bioguideId": member.bioguide_id, "state": member.state, "party": member.party,
        "district": member.district if chamber == "house" else None,
        "votingRecord": {"keyVotes": votes, "recentVotes": [],
                         "effectiveParty": member.caucus_party or member.party},
        "funding": {}, "constituentReference": CONSTITUENT_REFERENCE.load(),
    }
    return calculate_scores(payload)["constituentAlignment"], calculate_confidence(payload)


def test_senate_scores_match_what_a_pipeline_run_stores(db_session):
    _make_stale("senate")
    _seed_senate(db_session)

    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]

    ref = CONSTITUENT_REFERENCE.load()["senate"]
    assert ref["statistic"] == CONSTITUENT_REFERENCE_STATISTIC and ref["n"] == 40
    db_session.expire_all()
    for s in db_session.query(Senator):
        score, confidence = _pipeline_scores(db_session, s, KeyVote, KeyVote.senator_id, "senate")
        assert s.score_constituent_alignment == score
        stored = json.loads(s.score_confidence)
        # The grade is kept; the vote-part status is the one a run computes.
        assert stored == {"constituentAlignment": "high",
                          "constituentAlignmentVotePart": confidence["constituentAlignmentVotePart"]}
    assert len({s.score_constituent_alignment for s in db_session.query(Senator)}) > 1


def test_house_scores_match_what_a_pipeline_run_stores(db_session):
    _make_stale("house")
    _seed_house(db_session)

    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["house"]

    assert CONSTITUENT_REFERENCE.load()["house"]["statistic"] == CONSTITUENT_REFERENCE_STATISTIC
    db_session.expire_all()
    reps = db_session.query(Representative).all()
    for r in reps:
        score, _ = _pipeline_scores(db_session, r, RepKeyVote, RepKeyVote.representative_id, "house")
        assert r.score_constituent_alignment == score
    assert len({r.score_constituent_alignment for r in reps}) > 1


@pytest.mark.parametrize("caucus", ["D", None])
def test_independent_scored_as_the_pipeline_scores_them(db_session, caucus):
    _make_stale("senate")
    _seed_senate(db_session, n=50)  # both parties stay measurable without S000
    s = db_session.get(Senator, "S000")
    s.party, s.caucus_party = "I", caucus
    db_session.commit()

    rescore_stale_constituent_alignment(_factory(db_session))

    db_session.expire_all()
    s = db_session.get(Senator, "S000")
    score, _ = _pipeline_scores(db_session, s, KeyVote, KeyVote.senator_id, "senate")
    assert s.score_constituent_alignment == score


def test_votes_on_one_bill_each_count(db_session):
    # Cloture and passage on one bill are two roll calls; stored rows are
    # already one per roll call, so both count (row identity, not bill id).
    _make_stale("senate")
    _seed_senate(db_session)
    db_session.add(KeyVote(senator_id="S001", bill_name="Bill 0", bill_id="b-0", date="2025-03-02",
                           vote="Nay", voted_with_party=False))
    db_session.commit()

    rescore_stale_constituent_alignment(_factory(db_session))

    db_session.expire_all()
    s = db_session.get(Senator, "S001")
    assert sc.party_break_rate({"keyVotes": [
        stored_vote(v.id, v.bill_id, v.voted_with_party)
        for v in db_session.query(KeyVote).filter(KeyVote.senator_id == "S001")]})[1] == 31
    score, _ = _pipeline_scores(db_session, s, KeyVote, KeyVote.senator_id, "senate")
    assert s.score_constituent_alignment == score


def test_empty_confidence_gets_the_status(db_session):
    # Every senator on main was stored with score_confidence "{}".
    _make_stale("senate")
    _seed_senate(db_session, confidence="{}")
    rescore_stale_constituent_alignment(_factory(db_session))
    db_session.expire_all()
    assert all(set(json.loads(s.score_confidence)) == {"constituentAlignmentVotePart"}
               for s in db_session.query(Senator))


def test_only_the_stale_chamber_is_rescored(db_session):
    _make_stale("senate")
    house_before = CONSTITUENT_REFERENCE.load()["house"]
    CONSTITUENT_REFERENCE.write("house", house_before)
    _seed_senate(db_session)
    _seed_house(db_session)

    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]

    house_after = {k: v for k, v in CONSTITUENT_REFERENCE.load()["house"].items() if k != "computed_at"}
    assert house_after == {k: v for k, v in house_before.items() if k != "computed_at"}
    db_session.expire_all()
    assert {r.score_constituent_alignment for r in db_session.query(Representative)} == {100.0}


def test_current_or_absent_reference_is_left_alone(db_session):
    _seed_senate(db_session)
    # No persisted entry: scored against the bundled prior, as the breakdown reads.
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    CONSTITUENT_REFERENCE.write("senate", CONSTITUENT_REFERENCE.load()["senate"])
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    db_session.expire_all()
    assert {s.score_constituent_alignment for s in db_session.query(Senator)} == {100.0}


def test_reference_is_persisted_only_after_the_scores_commit(db_session, monkeypatch):
    _make_stale("senate")
    _seed_senate(db_session)
    real, fail = sc._constituent_alignment_core, [True]

    def flaky(*a, **k):
        if fail[0]:
            raise RuntimeError("scoring failed")
        return real(*a, **k)

    monkeypatch.setattr(sc, "_constituent_alignment_core", flaky)
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    # Still stale (so the next startup retries), scores untouched.
    assert "statistic" not in json.loads(CONSTITUENT_REFERENCE.live_path.read_text())["senate"]
    db_session.expire_all()
    assert {s.score_constituent_alignment for s in db_session.query(Senator)} == {100.0}

    fail[0] = False
    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]


def test_a_live_run_blocks_only_its_own_chamber(db_session):
    _make_stale("senate")
    _make_stale("house")
    _seed_senate(db_session)
    _seed_house(db_session)
    db_session.add(HousePipelineRun(status=PipelineStatus.RUNNING))
    db_session.commit()
    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]


def test_an_orphaned_run_does_not_block(db_session):
    _make_stale("senate")
    _seed_senate(db_session)
    db_session.add(PipelineRun(status=PipelineStatus.RUNNING, started_at=utcnow() - timedelta(hours=13)))
    db_session.commit()
    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]


def test_too_few_members_to_measure_leaves_scores(db_session):
    _make_stale("senate")
    _seed_senate(db_session, n=4)
    assert rescore_stale_constituent_alignment(_factory(db_session)) == []
    db_session.expire_all()
    assert {s.score_constituent_alignment for s in db_session.query(Senator)} == {100.0}


def test_the_signal_overlap_is_re_measured_after_a_rescore(db_session, monkeypatch):
    # The overlap check reads the breakdowns the rescore just moved; without
    # this /about/scores kept the pre-rescore reading until the next run.
    import app.pipeline.analyze.signal_overlap as so

    measured = []
    monkeypatch.setattr(so, "record_signal_overlap", lambda db, chamber: measured.append(chamber))
    _make_stale("senate")
    _seed_senate(db_session)

    assert rescore_stale_constituent_alignment(_factory(db_session)) == ["senate"]
    assert measured == ["senate"]
