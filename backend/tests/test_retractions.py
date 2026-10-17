"""The public retraction log: withdrawn issues answer 410 with the reason."""

from fastapi.testclient import TestClient

from app import retractions
from app.database import get_db
from app.main import app


def test_the_log_is_well_formed():
    for entry in retractions.entries():
        assert entry["reason"] and entry["date"]
        assert len(entry["issueIds"]) == len(entry["publicIds"])
        # Withdrawn issues are deleted by a data migration; a withdrawn race
        # story and its post are only hidden, so they need none.
        assert bool(entry["migration"]) == bool(entry["issueIds"])
        assert entry["issueIds"] or entry["coverageItems"] or entry["broadcastPostIds"]
        assert "PR #PRNUM" not in entry["reason"]


def test_the_ny14_post_and_its_story_are_withdrawn():
    assert 114 in retractions.withdrawn_post_ids()
    assert any("/newsletters/morning-report/6153128-" in u for u in retractions.withdrawn_coverage_urls())


def test_a_withdrawn_story_is_left_off_the_race_page_and_its_post_out_of_the_feed(db_session, monkeypatch):
    from app.api.elections import _coverage_is_displayable
    from app.broadcast import not_withdrawn
    from app.models import BroadcastPost, Race, RaceCoverageItem
    from app.time_utils import utcnow

    monkeypatch.setattr(retractions, "entries", lambda: [{
        "publicIds": [], "issueIds": [], "broadcastPostIds": [2],
        "coverageItems": [{"id": 1, "url": "https://example.com/withdrawn"}],
    }])
    db_session.add(Race(id="2026-HOUSE-NY-14", cycle_year=2026, office="H", state="NY"))
    for n, url in enumerate(("https://example.com/withdrawn", "https://example.com/kept"), 1):
        db_session.add(RaceCoverageItem(
            race_id="2026-HOUSE-NY-14", source_type="news", source_name="AP", title=f"t{n}", url=url,
            match_basis="full_name", relevance=0.9,
        ))
        db_session.add(BroadcastPost(
            id=n, kind="race", subject="race:2026-HOUSE-NY-14", title="t", text=f"post {n}",
            url="https://civitas-research.org/elections/states/NY", published_at=utcnow(),
        ))
    db_session.commit()
    shown = db_session.query(RaceCoverageItem.url).filter(_coverage_is_displayable(db_session)).all()
    assert [u for (u,) in shown] == ["https://example.com/kept"]
    # The race's other post, which shares the withdrawn post's subject, stays.
    assert [p.id for p in db_session.query(BroadcastPost).filter(not_withdrawn())] == [1]


def test_a_withdrawn_issue_is_found_by_either_id():
    assert retractions.retraction_for_issue("i1679cbc7")["date"] == "2026-09-27"
    assert retractions.retraction_for_issue("759") is not None
    assert retractions.retraction_for_issue("i0000000") is None


def test_a_withdrawn_issue_answers_410_with_its_reason(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        r = TestClient(app).get("/api/action/issues/i1679cbc7")
        assert r.status_code == 410
        assert r.json()["detail"]["retracted"] is True
        assert "unrelated news stories" in r.json()["detail"]["reason"]
        assert TestClient(app).get("/api/action/issues/i0000001").status_code == 404
    finally:
        app.dependency_overrides.clear()
