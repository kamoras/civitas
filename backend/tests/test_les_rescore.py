"""Startup rescore of Legislative Effectiveness onto the current reference."""

from sqlalchemy.orm import sessionmaker

from app.models import PipelineRun, PipelineStatus, Senator, SponsoredBill
from app.pipeline.analyze.population_reference import LES_REFERENCE
from app.pipeline.les_rescore import rescore_stale_legislative_effectiveness


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False)


def _seed(db_session, n=35):
    for i in range(n):
        s = Senator(
            id=f"S{i:03d}", bioguide_id=f"S{i:03d}", name=f"Senator {i}", state="CT",
            party="R" if i % 2 else "D", years_in_office=4, is_current=True,
            score_legislative_effectiveness=50.0,
        )
        db_session.add(s)
        for j in range(i % 12 + 1):
            db_session.add(SponsoredBill(
                senator_id=s.id, bill_id=f"S.{i}-{j}", title="A bill", congress=119,
                bill_type="s", is_law=False, latest_action="Introduced",
                stage="ENACTED" if (i % 7 == 0 and j == 0) else "INTRODUCED",
            ))
    db_session.commit()


def test_old_scale_reference_is_remeasured_and_scores_rescored(db_session, pinned_population_references):
    old = {k: v for k, v in LES_REFERENCE.load()["senate"].items() if k not in ("stage_totals", "n_members")}
    LES_REFERENCE.write("senate", old)
    _seed(db_session)

    assert rescore_stale_legislative_effectiveness(_factory(db_session)) == ["senate"]
    ref = LES_REFERENCE.load()["senate"]
    assert ref.get("stage_totals") and ref["n_members"] == 35
    db_session.expire_all()
    scores = {s.id: s.score_legislative_effectiveness for s in db_session.query(Senator)}
    assert len(set(scores.values())) > 5  # rescored, not left at the seeded 50
    # A sponsor with an enacted bill outscores a same-status peer with more
    # bills that were only introduced (S014: 3 bills, one law; S022: 11 bills).
    assert scores["S014"] > scores["S022"]


def test_current_scale_reference_is_left_alone(db_session, pinned_population_references):
    _seed(db_session)
    assert rescore_stale_legislative_effectiveness(_factory(db_session)) == []
    assert {s.score_legislative_effectiveness for s in db_session.query(Senator)} == {50.0}


def test_skipped_while_a_pipeline_run_is_in_progress(db_session, pinned_population_references):
    old = {k: v for k, v in LES_REFERENCE.load()["senate"].items() if k not in ("stage_totals", "n_members")}
    LES_REFERENCE.write("senate", old)
    _seed(db_session)
    db_session.add(PipelineRun(status=PipelineStatus.RUNNING))
    db_session.commit()
    assert rescore_stale_legislative_effectiveness(_factory(db_session)) == []
