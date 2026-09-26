"""Matching a disclosure filer's printed name to a sitting member."""

from app.models import Representative, Senator
from app.pipeline.filer_matching import match_representative, match_senator


def _senators(db, *names):
    for i, name in enumerate(names):
        db.add(Senator(id=f"S{i}", name=name, state="XX", party="D", is_current=True))
    db.commit()


def test_accents_are_folded(db_session):
    """eFD prints "LUJAN"; the roster says "Luján". SQL ILIKE can't see that."""
    _senators(db_session, "Ben Ray Luján", "Tammy Baldwin")
    assert match_senator(db_session, "LUJAN", "BEN RAY").name == "Ben Ray Luján"


def test_same_surname_senators_are_told_apart_by_first_name(db_session):
    _senators(db_session, "Rick Scott", "Tim Scott")
    assert match_senator(db_session, "Scott", "Tim").name == "Tim Scott"
    assert match_senator(db_session, "Scott", "Rick").name == "Rick Scott"


def test_the_office_columns_display_name_disambiguates_a_formal_first_name(db_session):
    """A formal filing name ("Richard") isn't in a display name ("Rick
    Scott"); the eFD row's office column carries the display name."""
    _senators(db_session, "Rick Scott", "Tim Scott")
    assert match_senator(db_session, "Scott", "Richard") is None  # ambiguous: skipped, never guessed
    assert match_senator(db_session, "Scott", "Richard", "Scott, Rick (Senator)").name == "Rick Scott"


def test_surname_must_be_a_whole_word(db_session):
    _senators(db_session, "Angus King", "Amy Klobuchar")
    assert match_senator(db_session, "Kin", "Angus") is None


def test_multiword_surname(db_session):
    _senators(db_session, "Catherine Cortez Masto")
    assert match_senator(db_session, "Cortez Masto", "Catherine").name == "Catherine Cortez Masto"


def test_representative_accent_and_district(db_session):
    db_session.add(Representative(id="R1", name="Nydia M. Velázquez", state="NY", district=7, party="D", is_current=True))
    db_session.add(Representative(id="R2", name="Other Velazquez", state="NY", district=8, party="D", is_current=True))
    db_session.commit()
    assert match_representative(db_session, "Velazquez", "Nydia M.", "NY07").id == "R1"
