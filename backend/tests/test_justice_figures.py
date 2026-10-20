"""A justice's figures beside the sitting justices' (services/justice_figures.py),
and the justices a president appointed. Justices and presidents here are made
up: the Court's membership comes from the data."""

from datetime import datetime

from app.models import Justice, JusticeAppointment, President
from app.pipeline.fetch.justice_records import fjc_appointments
from app.pipeline.justice_pipeline import store_court_seats
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


def _fjc(last, first, name, seat, nominated, start, until=None, nid="1"):
    return {"last": last, "first": first, "name": name, "seat": seat, "nid": nid,
            "nominated": nominated, "from": start, "until": until}


def test_fjc_rows_carry_the_name_and_seat():
    rows = [{"nid": "42", "Last Name": "Example", "First Name": "Jane", "Middle Name": "Quinn", "Suffix": "Jr.",
             "Court Type (1)": "Supreme Court", "Appointment Title (1)": "Chief Justice",
             "Nomination Date (1)": "1/5/2001", "Commission Date (1)": "2/1/2001", "Termination Date (1)": ""}]
    [a] = fjc_appointments(rows)
    assert (a["nid"], a["name"], a["seat"], a["until"]) == ("42", "Jane Q. Example, Jr.", "Chief Justice", None)


def test_appointments_are_stored_with_the_president_in_office_on_the_nomination_date(db_session):
    # One president twice, with someone else between: each appointment
    # goes to the presidency it was made in.
    db_session.add_all([
        President(id="first-1", name="First President", party="R", number=1,
                  term_start="1901-03-04", term_end="1905-03-04"),
        President(id="middle-2", name="Middle President", party="D", number=2,
                  term_start="1905-03-04", term_end="1909-03-04"),
        President(id="first-3", name="First President", party="R", number=3, term_start="1909-03-04"),
    ])
    db_session.commit()
    stored = store_court_seats(db_session, [
        _fjc("early", "A", "Ada Early", "Associate Justice", "1902-01-10", "1902-02-01", "1930-01-01", nid="1"),
        _fjc("later", "B", "Ben Later", "Chief Justice", "1910-05-01", "1910-06-01", "1921-01-01", nid="2"),
        _fjc("between", "C", "Cy Between", "Associate Justice", "1906-01-01", "1906-02-01", "1920-01-01", nid="3"),
    ], db_session.query(President).all())
    assert stored == 3
    by = {a.fjc_nid: a.president_id for a in db_session.query(JusticeAppointment)}
    assert by == {"1": "first-1", "2": "first-3", "3": "middle-2"}
    # A historical president's page lists every appointment, plain text
    # where the site holds no page for the justice.
    first = justices_appointed_by(db_session, "first-1")
    assert first == [{"id": None, "name": "Ada Early", "seat": "Associate Justice", "dateStart": "1902-02-01",
                      "dateEnd": "1930-01-01", "isActive": False, "photo": None}]
    assert [a["name"] for a in justices_appointed_by(db_session, "first-3")] == ["Ben Later"]
    # An outage, or a malformed read, keeps what is stored.
    assert store_court_seats(db_session, None, db_session.query(President).all()) is None
    assert store_court_seats(db_session, [{"last": "x"}], db_session.query(President).all()) is None
    assert db_session.query(JusticeAppointment).count() == 3


def test_a_president_lists_the_justices_appointed_linking_their_pages(db_session):
    _figures(db_session)
    # The stored name ends with a suffix, which the match must see past.
    left = db_session.get(Justice, "left")
    left.name, left.last_name = "Lee Left, Jr.", "Left, Jr."
    # "left" (seated 2010-01-01) and a former justice the site never held.
    store_court_seats(db_session, [
        _fjc("left", "L", "Lee Left", "Associate Justice", "2009-11-01", "2010-01-02", nid="1"),
        _fjc("gone", "G", "Gil Gone", "Chief Justice", "2009-06-01", "2009-08-01", "2012-01-01", nid="2"),
    ], db_session.query(President).all())
    db_session.commit()
    rows = justices_appointed_by(db_session, "first-1")
    assert [(r["id"], r["name"]) for r in rows] == [(None, "Gil Gone"), ("left", "Lee Left, Jr.")]
    assert rows[1]["isActive"] is True and rows[1]["photo"]
    assert justices_appointed_by(db_session, "nobody") is None
    assert _get(db_session, "/api/justices/appointed-by/first-1").json()[1]["id"] == "left"
    assert _get(db_session, "/api/justices/appointed-by/nobody").status_code == 404
    # The justice's own page links the presidency the seat was filled in.
    assert justice_figures(db_session, "left").appointed_by.id == "first-1"


def test_the_profile_carries_the_figures(db_session):
    _figures(db_session)
    body = _get(db_session, "/api/politicians/middle").json()
    assert body["scorecard"]["figures"]["measures"][0]["key"] == "majority"
