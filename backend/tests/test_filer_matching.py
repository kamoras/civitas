"""Matching a disclosure filer's printed name to a sitting member."""

from app.models import Representative, Senator
from app.pipeline.filer_matching import (
    current_representatives,
    current_senators,
    match_representative,
    match_senator,
)


def _senators(db, *names):
    for i, name in enumerate(names):
        db.add(Senator(id=f"S{i}", name=name, state="XX", party="D", is_current=True))
    db.commit()


def test_accents_are_folded(db_session):
    """eFD prints "MONTANO"; the roster says "Montaño". SQL ILIKE can't see that."""
    _senators(db_session, "Ben Ray Montaño", "Tess Bradwell")
    assert match_senator(current_senators(db_session), "MONTANO", "BEN RAY").name == "Ben Ray Montaño"


def test_same_surname_senators_are_told_apart_by_first_name(db_session):
    _senators(db_session, "Rex Scott", "Tad Scott")
    assert match_senator(current_senators(db_session), "Scott", "Tad").name == "Tad Scott"
    assert match_senator(current_senators(db_session), "Scott", "Rex").name == "Rex Scott"


def test_the_office_columns_display_name_disambiguates_a_formal_first_name(db_session):
    """A formal filing name ("Reginald") isn't in a display name ("Rex
    Scott"); the eFD row's office column carries the display name."""
    _senators(db_session, "Rex Scott", "Tad Scott")
    assert match_senator(current_senators(db_session), "Scott", "Reginald") is None  # ambiguous: skipped, never guessed
    assert match_senator(current_senators(db_session), "Scott", "Reginald", "Scott, Rex (Senator)").name == "Rex Scott"


def test_surname_must_be_a_whole_word(db_session):
    _senators(db_session, "Angus King", "Amy Kowalczyk")
    assert match_senator(current_senators(db_session), "Kin", "Angus") is None


def test_multiword_surname(db_session):
    _senators(db_session, "Elena Ruiz Ortega")
    assert match_senator(current_senators(db_session), "Ruiz Ortega", "Elena").name == "Elena Ruiz Ortega"


def test_representative_accent_and_district(db_session):
    db_session.add(Representative(id="R1", name="Noemi M. Vélez", state="NY", district=7, party="D", is_current=True))
    db_session.add(Representative(id="R2", name="Other Velez", state="NY", district=8, party="D", is_current=True))
    db_session.commit()
    assert match_representative(current_representatives(db_session), "Velez", "Noemi M.", "NY07").id == "R1"


def test_a_senator_is_not_matched_to_a_filer_after_leaving_office(db_session):
    db_session.add(Senator(id="S1", name="Pat Former", state="XX", party="D", is_current=False))
    db_session.commit()
    assert current_senators(db_session) == []


def test_filer_matcher_looks_each_filer_up_once(db_session):
    from app.pipeline.filer_matching import FilerMatcher

    _senators(db_session, "Tess Bradwell")
    calls = []

    def match(roster, *key):
        calls.append(key)
        return match_senator(roster, *key)

    matcher = FilerMatcher(current_senators(db_session), match)
    assert matcher("Bradwell", "Tess") == "S0"
    assert matcher("Bradwell", "Tess") == "S0"
    assert matcher("Nobody", "Here") is None
    assert calls == [("Bradwell", "Tess"), ("Nobody", "Here")]


def test_a_generational_suffix_after_the_surname_still_matches(db_session):
    """eFD's last-name field carries the suffix after a comma ("Doe, Jr.");
    the House index a credential ("Doe, MD, FACS")."""
    _senators(db_session, "Wendell Hartree", "Tess Bradwell")
    assert match_senator(current_senators(db_session), "Hartree, Jr.", "Wendell").id == "S0"
    assert match_senator(current_senators(db_session), "Hartree, IV", "W", "Hartree, Wendell (Senator)").id == "S0"
    db_session.add(Representative(id="R1", name="Neil P. Ostrander", state="FL", district=2, party="R", is_current=True))
    db_session.commit()
    assert match_representative(current_representatives(db_session), "Ostrander, MD, FACS", "Neil", "FL02").id == "R1"


def _reps(db, *rows):
    for rid, name, state, district in rows:
        db.add(Representative(id=rid, name=name, state=state, district=district, party="D", is_current=True))
    db.commit()


def test_a_member_filing_under_a_renumbered_district_is_matched_in_the_state(db_session):
    """The Clerk keeps a filer's registered district, so after a
    redistricting a member files under the old number."""
    _reps(db_session, ("R1", "Lucy Marchetti", "GA", 6), ("R2", "Richard Okafor", "GA", 7))
    roster = current_representatives(db_session)
    # Lucia/Lucy: no shared token, but the first names agree.
    assert match_representative(roster, "Marchetti", "Lucia Kay Ms", "GA07").id == "R1"
    assert match_representative(roster, "Okafor", "Richard Dean Dr", "GA06").id == "R2"


def test_a_different_person_with_a_members_surname_elsewhere_in_the_state_is_not_matched(db_session):
    _reps(db_session, ("R1", "Robert Ferreira", "CA", 42), ("R2", "Jonathan L. Brandt", "IL", 1))
    roster = current_representatives(db_session)
    # A former member in another district.
    assert match_representative(roster, "Ferreira", "Michael", "CA27") is None
    # A shared middle initial is not a first name that agrees.
    assert match_representative(roster, "Brandt", "Jesse L.", "IL02") is None


def test_the_district_still_decides_when_a_member_there_has_the_surname(db_session):
    _reps(db_session, ("R1", "Ann Velez", "NY", 7), ("R2", "Anna Velez", "NY", 8))
    assert match_representative(current_representatives(db_session), "Velez", "Anna", "NY07").id == "R1"
