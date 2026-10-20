"""A justice's figures beside the sitting justices' (services/justice_figures.py),
and the justices a president appointed. Justices and presidents here are made
up: the Court's membership comes from the data."""

from datetime import datetime

from app.models import Justice, President
from app.services.justice_figures import justice_figures, justices_appointed_by
from tests.test_court import ALL, _case, _get, _seat


def _figures(db):
    _seat(db)
    # Three cases: a 3-2 (decided by one vote), a 4-1 and a 5-0.
    _case(db, "c1", 2024, "2025-03-01", ALL[2:], ALL[:2], author="right")
    _case(db, "c2", 2024, "2025-04-01", ALL[1:], ALL[:1], author="middle")
    _case(db, "c3", 2024, "2025-05-01", ALL, author="middle")
    for j in db.query(Justice):
        votes = [v for v in j.votes]
        j.cases_decided = len(votes)
        j.majority_pct = 100 * sum(v.vote == "majority" for v in votes) / len(votes)
        j.dissent_pct = 100 - j.majority_pct
        j.authored_majority = sum(v.opinion_type == "majority" for v in votes)
    db.add(President(id="first-1", name="First President", party="R", number=1, term_start="2009-01-20"))
    left = db.get(Justice, "left")
    left.appointing_president, left.appointing_party = "First President", "R"
    left.appointer_effect, left.appointer_effect_se = 0.05, 0.02
    db.get(Justice, "right").appointer_effect = -0.01
    db.get(Justice, "right").appointer_effect_se = 0.03
    db.commit()


def test_each_figure_sits_beside_every_sitting_justices(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 1, 1, 12))
    _figures(db_session)
    f = justice_figures(db_session, "middle")
    by = {m.key: m for m in f.measures}
    assert f.peers == 5 and f.terms == [2024]
    maj = by["majority"]
    assert maj.shown == "100%" and maj.record == 3 and len(maj.peers) == 5
    assert [p.value for p in maj.peers] == sorted(p.value for p in maj.peers)
    assert maj.below == 2 and "in dissent in 0%" in maj.sentence
    # Three justices share the top figure.
    assert "Tied for highest of the 5 sitting justices" in maj.sentence
    # One-vote cases: each vote in the 3-2 majority decided it.
    assert by["decisive"].shown == "1 of 1" and by["decisive"].record == 1
    assert by["authoredMajority"].value == 2
    # Seated 2012-01-01, read on 2026-01-01.
    assert by["tenure"].shown == "14 years" and "since 2012" in by["tenure"].sentence
    assert by["position"].shown == "+0.20" and by["position"].median_shown == "+0.20"
    # No appointer estimate for this justice: nothing to draw.
    assert f.appointer is None and f.appointed_by is None


def test_the_appointer_estimate_comes_with_every_interval_and_the_presidents_page(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 1, 1, 12))
    _figures(db_session)
    f = justice_figures(db_session, "left")
    assert f.appointed_by.id == "first-1" and f.appointed_by.year == 2010
    assert [r.id for r in f.appointer.rows] == ["right", "left"]
    assert f.appointer.rows[1].ci_low == round(0.05 - 1.96 * 0.02, 4)
    assert "5.0 points more often" in f.appointer.sentence
    assert "—" not in f.appointer.sentence


def test_a_president_lists_the_justices_appointed(db_session):
    _figures(db_session)
    # The same president again after a gap: a justice seated in the second
    # term belongs to that term's page, not the first's.
    db_session.add(President(id="first-3", name="First President", party="R", number=3, term_start="2017-01-20"))
    db_session.get(President, "first-1").term_end = "2013-01-20"
    right = db_session.get(Justice, "right")
    right.appointing_president = "First President"
    right.date_start = "2018-01-01"
    db_session.commit()
    assert [j["id"] for j in justices_appointed_by(db_session, "first-3")] == ["right"]
    assert justice_figures(db_session, "right").appointed_by.id == "first-3"
    assert [j["id"] for j in justices_appointed_by(db_session, "first-1")] == ["left"]
    assert justices_appointed_by(db_session, "nobody") is None
    assert _get(db_session, "/api/justices/appointed-by/first-1").json()[0]["id"] == "left"
    assert _get(db_session, "/api/justices/appointed-by/nobody").status_code == 404


def test_the_profile_carries_the_figures(db_session):
    _figures(db_session)
    body = _get(db_session, "/api/politicians/middle").json()
    assert body["scorecard"]["figures"]["measures"][0]["key"] == "majority"
