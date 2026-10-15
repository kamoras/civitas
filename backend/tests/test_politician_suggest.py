"""The member 404's suggestions (/politicians/suggest): the politicians
whose page addresses are closest to one that matched no one."""
import json

from app.api.politicians import suggest_politicians
from app.models import Justice, President, Representative, Senator


def _ids(db, q):
    return [s["id"] for s in json.loads(suggest_politicians(q=q, db=db).body)["suggestions"]]


def _seed(db):
    db.add_all([
        Senator(id="pat-kelley", name="Pat Kelley", state="AZ", party="D", is_current=True),
        Representative(id="lee-kelley", name="Lee Kelley", state="IL", party="D", district=2, is_current=True),
        Representative(id="dana-moss", name="Dana Moss", state="TX", party="R", district=5, is_current=True),
        President(id="able-44", name="Sam Able", party="D", number=44, term_start="2009-01-20", is_current=False),
        Justice(id="jo_q_baker_jr", name="Jo Q. Baker, Jr.", last_name="Baker"),
    ])
    db.commit()


def test_a_typo_in_a_full_address_offers_that_politician(db_session):
    _seed(db_session)
    assert _ids(db_session, "pat-kelly") == ["pat-kelley"]


def test_a_surname_alone_offers_everyone_with_it(db_session):
    _seed(db_session)
    assert _ids(db_session, "kelley") == ["lee-kelley", "pat-kelley"]
    assert _ids(db_session, "baker") == ["jo_q_baker_jr"]
    assert _ids(db_session, "able") == ["able-44"]


def test_nothing_close_offers_nobody(db_session):
    _seed(db_session)
    assert _ids(db_session, "zzzz") == []
    assert _ids(db_session, "--") == []


def test_says_who_each_one_is(db_session):
    _seed(db_session)
    body = json.loads(suggest_politicians(q="dana-mos", db=db_session).body)
    assert body["suggestions"] == [
        {"id": "dana-moss", "name": "Dana Moss", "role": "Representative", "party": "R", "state": "TX"},
    ]
