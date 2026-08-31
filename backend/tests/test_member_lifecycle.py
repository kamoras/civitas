"""Roster reconciliation and eventual removal of departed members."""

import json

import pytest

from app.models import (
    ActionIssue,
    BskySenatorSpotlight,
    Donor,
    ExploreDocument,
    President,
    Representative,
    ScoreSnapshot,
    Senator,
)
from app.pipeline.member_lifecycle import (
    CHAMBER_HOUSE,
    CHAMBER_SENATE,
    RETIREMENT_GRACE_DAYS,
    purge_departed_members,
    reconcile_roster,
)

TODAY = "2026-07-27"
# Comfortably past the grace period, and just inside it.
LONG_AGO = "2025-01-01"
RECENT = "2026-06-01"


def _senator(db, sid, bioguide, *, is_current=True, left=None):
    s = Senator(
        id=sid, bioguide_id=bioguide, name=sid.replace("-", " ").title(),
        state="CA", party="D", is_current=is_current, left_office_date=left,
    )
    db.add(s)
    return s


def _rep(db, rid, bioguide, *, is_current=True, left=None):
    r = Representative(
        id=rid, bioguide_id=bioguide, name=rid.replace("-", " ").title(),
        state="CA", party="D", district=1, is_current=is_current, left_office_date=left,
    )
    db.add(r)
    return r


def _roster(*bioguides):
    """A roster big enough to clear the too-small-to-trust guard."""
    return set(bioguides) | {f"X{i:05d}" for i in range(50)}


# ── reconcile_roster ────────────────────────────────────────────────

def test_member_absent_from_roster_is_marked_departed(db_session):
    _senator(db_session, "stays-here", "S00001")
    _senator(db_session, "gone-away", "S00002")
    db_session.flush()

    result = reconcile_roster(
        db_session, CHAMBER_SENATE, _roster("S00001"), today=TODAY,
    )

    assert result["departed"] == ["gone-away"]
    gone = db_session.query(Senator).filter_by(id="gone-away").one()
    assert gone.is_current is False
    assert gone.left_office_date == TODAY
    assert gone.vacancy_reason == "left office"
    assert db_session.query(Senator).filter_by(id="stays-here").one().is_current is True


def test_member_back_on_roster_is_restored(db_session):
    _senator(db_session, "returned", "S00001", is_current=False, left="2026-01-01")
    db_session.flush()

    result = reconcile_roster(
        db_session, CHAMBER_SENATE, _roster("S00001"), today=TODAY,
    )

    assert result["restored"] == ["returned"]
    back = db_session.query(Senator).filter_by(id="returned").one()
    assert back.is_current is True
    # Purge clock must stop, or a restored member is deleted months later.
    assert back.left_office_date is None
    assert back.vacancy_reason is None


def test_partial_roster_fetch_retires_nobody(db_session):
    for i in range(20):
        _senator(db_session, f"sen-{i}", f"S{i:05d}")
    db_session.flush()

    # Only two members came back — a broken fetch, not 18 resignations.
    result = reconcile_roster(
        db_session, CHAMBER_SENATE, {"S00000", "S00001"}, today=TODAY,
    )

    assert result["status"] == "skipped"
    assert result["departed"] == []
    assert db_session.query(Senator).filter_by(is_current=False).count() == 0


def test_partial_roster_fetch_raises_an_ops_alert(db_session, monkeypatch):
    """A silent skip could run for weeks — fetch_senators caches whatever
    it got and breaks out of pagination without erroring."""
    sent = []
    monkeypatch.setattr(
        "app.ops_alerts.send_ops_alert",
        lambda subject, body, **kw: sent.append(subject) or True,
    )
    for i in range(20):
        _senator(db_session, f"sen-{i}", f"S{i:05d}")
    db_session.flush()

    reconcile_roster(db_session, CHAMBER_SENATE, {"S00000"}, today=TODAY)

    assert sent and "roster reconciliation skipped" in sent[0].lower()


def test_alert_failure_does_not_break_the_pipeline(db_session, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("app.ops_alerts.send_ops_alert", _boom)
    for i in range(20):
        _senator(db_session, f"sen-{i}", f"S{i:05d}")
    db_session.flush()

    result = reconcile_roster(db_session, CHAMBER_SENATE, {"S00000"}, today=TODAY)

    assert result["status"] == "skipped"


def test_empty_roster_retires_nobody(db_session):
    _senator(db_session, "sen-a", "S00001")
    db_session.flush()

    result = reconcile_roster(db_session, CHAMBER_SENATE, set(), today=TODAY)

    assert result["status"] == "skipped"
    assert db_session.query(Senator).filter_by(id="sen-a").one().is_current is True


def test_freshman_class_turnover_is_not_blocked(db_session):
    """A new Congress replaces members rather than shrinking the chamber,
    so a large legitimate departure batch must still go through."""
    for i in range(100):
        _senator(db_session, f"sen-{i}", f"S{i:05d}")
    db_session.flush()

    # 30 members leave, 30 new ones arrive — roster size holds at 100.
    roster = {f"S{i:05d}" for i in range(30, 100)} | {f"N{i:05d}" for i in range(30)}
    result = reconcile_roster(db_session, CHAMBER_SENATE, roster, today=TODAY)

    assert result["status"] == "ok"
    assert len(result["departed"]) == 30


def test_member_without_bioguide_id_is_left_alone(db_session):
    _senator(db_session, "no-bioguide", "")
    db_session.flush()

    result = reconcile_roster(db_session, CHAMBER_SENATE, _roster("S00001"), today=TODAY)

    assert result["departed"] == []
    assert result["unmatchable"] == 1
    assert db_session.query(Senator).filter_by(id="no-bioguide").one().is_current is True


def test_reconcile_rejects_branches_it_must_not_touch(db_session):
    for branch in ("president", "scotus", "justice"):
        with pytest.raises(ValueError, match="chamber must be one of"):
            reconcile_roster(db_session, branch, _roster("S00001"), today=TODAY)


# ── purge_departed_members ──────────────────────────────────────────

def test_member_within_grace_period_is_kept(db_session):
    _senator(db_session, "recently-gone", "S00001", is_current=False, left=RECENT)
    db_session.flush()

    result = purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)

    assert result["purged"] == []
    assert db_session.query(Senator).filter_by(id="recently-gone").count() == 1


def test_member_past_grace_period_is_deleted(db_session):
    _senator(db_session, "long-gone", "S00001", is_current=False, left=LONG_AGO)
    db_session.flush()

    result = purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)

    assert result["purged"] == ["long-gone"]
    assert db_session.query(Senator).filter_by(id="long-gone").count() == 0


def test_serving_member_is_never_purged(db_session):
    _senator(db_session, "serving", "S00001", left=LONG_AGO)
    db_session.flush()

    purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)

    assert db_session.query(Senator).filter_by(id="serving").count() == 1


def test_grace_boundary_is_exactly_the_configured_window(db_session):
    from datetime import datetime, timedelta

    cutoff = datetime.strptime(TODAY, "%Y-%m-%d") - timedelta(days=RETIREMENT_GRACE_DAYS)
    _senator(db_session, "on-cutoff", "S00001", is_current=False,
             left=cutoff.strftime("%Y-%m-%d"))
    _senator(db_session, "day-after", "S00002", is_current=False,
             left=(cutoff + timedelta(days=1)).strftime("%Y-%m-%d"))
    db_session.flush()

    result = purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)

    assert result["purged"] == ["on-cutoff"]


def test_manual_vacancy_without_a_date_gets_one_stamped(db_session):
    """Admin-marked vacancies predate automatic detection and carry no
    date — they start the clock instead of being purged or ignored."""
    _senator(db_session, "old-manual", "S00001", is_current=False, left=None)
    db_session.flush()

    result = purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)

    assert result["purged"] == []
    assert result["stamped"] == ["old-manual"]
    assert db_session.query(Senator).filter_by(id="old-manual").one().left_office_date == TODAY


@pytest.mark.parametrize("bad_date", ["2026", "07/01/2026", "unknown", "2026-13-45"])
def test_malformed_departure_date_never_triggers_a_delete(db_session, bad_date):
    """The cutoff test is a string comparison, so a plausible admin typo
    can sort below any real cutoff. Those must be restamped, not acted on
    — deleting a member takes their donors, votes and bills with them."""
    _senator(db_session, "typo-date", "S00001", is_current=False, left=bad_date)
    db_session.add(Donor(senator_id="typo-date", name="Acme PAC", total=1.0, type="PAC"))
    db_session.flush()

    result = purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)

    assert result["purged"] == []
    assert result["stamped"] == ["typo-date"]
    assert db_session.query(Senator).filter_by(id="typo-date").one().left_office_date == TODAY
    assert db_session.query(Donor).filter_by(senator_id="typo-date").count() == 1


def test_purge_removes_child_rows(db_session):
    _senator(db_session, "long-gone", "S00001", is_current=False, left=LONG_AGO)
    db_session.add(Donor(senator_id="long-gone", name="Acme PAC", total=1.0, type="PAC"))
    db_session.flush()

    purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)
    db_session.flush()

    assert db_session.query(Donor).filter_by(senator_id="long-gone").count() == 0


def test_purge_clears_references_no_foreign_key_covers(db_session):
    _senator(db_session, "long-gone", "S00001", is_current=False, left=LONG_AGO)
    db_session.add(ScoreSnapshot(
        entity_type="senator", entity_id="long-gone", date=LONG_AGO, overall_score=50.0,
    ))
    db_session.add(BskySenatorSpotlight(senator_id="long-gone"))
    db_session.add(ExploreDocument(
        doc_type="Senate Floor Speech", source="GovInfo", title="A speech",
        date=LONG_AGO, politician_id="long-gone", politician_name="Long Gone",
    ))
    db_session.add(ActionIssue(
        date=TODAY, rank=1, title="An issue",
        related_senators=json.dumps([
            {"id": "long-gone", "name": "Long Gone"},
            {"id": "still-here", "name": "Still Here"},
        ]),
    ))
    db_session.flush()

    purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)
    db_session.flush()

    assert db_session.query(ScoreSnapshot).filter_by(entity_id="long-gone").count() == 0
    assert db_session.query(BskySenatorSpotlight).filter_by(senator_id="long-gone").count() == 0

    # The speech survives as a government record; only the dead profile link goes.
    doc = db_session.query(ExploreDocument).one()
    assert doc.politician_id is None
    assert doc.politician_name == "Long Gone"

    related = json.loads(db_session.query(ActionIssue).one().related_senators)
    assert [e["id"] for e in related] == ["still-here"]


def test_purge_leaves_another_chambers_snapshots_alone(db_session):
    """Senator and representative ids are both name-derived, so they can
    collide — the snapshot delete must be scoped by entity_type."""
    _senator(db_session, "john-smith", "S00001", is_current=False, left=LONG_AGO)
    _rep(db_session, "john-smith", "H00001")
    db_session.add(ScoreSnapshot(
        entity_type="representative", entity_id="john-smith", date=LONG_AGO, overall_score=60.0,
    ))
    db_session.flush()

    purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)
    db_session.flush()

    assert db_session.query(ScoreSnapshot).filter_by(entity_type="representative").count() == 1
    assert db_session.query(Representative).filter_by(id="john-smith").count() == 1


def test_purge_leaves_another_chambers_spotlight_row_alone(db_session):
    """BskySenatorSpotlight holds both chambers, keyed on (senator_id,
    chamber) together — see bluesky_spotlight._pick_politician. A purge
    scoped by id alone would delete a live, different-chamber member's
    spotlight row just because the id string happens to match."""
    _senator(db_session, "john-smith", "S00001", is_current=False, left=LONG_AGO)
    _rep(db_session, "john-smith", "H00001")
    db_session.add(BskySenatorSpotlight(senator_id="john-smith", chamber="senate"))
    db_session.add(BskySenatorSpotlight(senator_id="john-smith", chamber="house"))
    db_session.flush()

    purge_departed_members(db_session, CHAMBER_SENATE, today=TODAY)
    db_session.flush()

    remaining = db_session.query(BskySenatorSpotlight).all()
    assert [(r.senator_id, r.chamber) for r in remaining] == [("john-smith", "house")]


def test_house_purge_also_clears_the_departed_reps_spotlight_row(db_session):
    """The chamber==CHAMBER_SENATE-only guard this replaces meant a
    departed representative's spotlight row was never cleaned up at all —
    silently orphaned forever, asymmetric with how senators were handled."""
    _rep(db_session, "long-gone", "H00001", is_current=False, left=LONG_AGO)
    db_session.add(BskySenatorSpotlight(senator_id="long-gone", chamber="house"))
    db_session.flush()

    purge_departed_members(db_session, CHAMBER_HOUSE, today=TODAY)
    db_session.flush()

    assert db_session.query(BskySenatorSpotlight).filter_by(senator_id="long-gone").count() == 0


def test_house_purge_uses_the_house_snapshot_entity_type(db_session):
    _rep(db_session, "long-gone", "H00001", is_current=False, left=LONG_AGO)
    db_session.add(ScoreSnapshot(
        entity_type="representative", entity_id="long-gone", date=LONG_AGO, overall_score=60.0,
    ))
    db_session.flush()

    result = purge_departed_members(db_session, CHAMBER_HOUSE, today=TODAY)
    db_session.flush()

    assert result["purged"] == ["long-gone"]
    assert db_session.query(ScoreSnapshot).filter_by(entity_id="long-gone").count() == 0


def test_purge_rejects_branches_it_must_not_touch(db_session):
    """Presidents are permanent site content — there is no code path that
    can reach them through this module."""
    db_session.add(President(
        id="obama-44", name="Barack Obama", party="D", number=44,
        term_start="2009-01-20", term_end="2017-01-20", is_current=False,
    ))
    db_session.flush()

    with pytest.raises(ValueError, match="chamber must be one of"):
        purge_departed_members(db_session, "president", today=TODAY)

    assert db_session.query(President).count() == 1
