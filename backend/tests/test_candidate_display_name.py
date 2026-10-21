"""A candidate's name as shown: the state's printing verbatim, else the
FEC filing's capitals as a reader writes them (app/name_casing.py)."""

from types import SimpleNamespace

import pytest

from app import name_casing
from app.api.elections import _candidate_summary
from app.models import Candidate, Representative
from app.name_casing import candidate_display_name, person_display_name


@pytest.mark.parametrize(("filed", "shown"), [
    ("GOLDMAN, CRAIG", "Craig Goldman"),
    ("RODRIGUEZ PRILLIMAN, ANGELA", "Angela Rodriguez Prilliman"),
    # Middle initials kept as filed; a suffix goes last, wherever it was.
    ("COLLINS, MICHAEL A JR", "Michael A Collins Jr."),
    ("SMITH JR., JOHN", "John Smith Jr."),
    ("DOE, JANE III", "Jane Doe III"),
    ("LEE, JR.", "Lee Jr."),
    # Titles the form allows are dropped.
    ("DOE, JOHN MR.", "John Doe"),
    ("DR. ROE, JANE", "Jane Roe"),
    # A lone V is a middle initial, not "the fifth".
    ("ROE, JOHN V", "John V Roe"),
    # Mc/Mac and O' names, the table's form first.
    ("MCDONNELL, PAT", "Pat McDonnell"),
    ("MACARTHUR, TOM", "Tom MacArthur"),
    ("MCBATH, LUCY", "Lucy McBath"),
    ("O'BRIEN, MARY", "Mary O'Brien"),
    ("D’ANGELO, SAM", "Sam D’Angelo"),
    # Hyphens and quoted nicknames.
    ("SMITH-JONES, ANNE", "Anne Smith-Jones"),
    ('DELGADO, MARTIN ALLEN "ROB"', 'Martin Allen "Rob" Delgado'),
    ("OSSOFF, T. JONATHAN", "T. Jonathan Ossoff"),
    # No comma: already first-last; spacing tidied.
    ("JANE  DOE", "Jane Doe"),
    # Not in capitals: a printing, left alone.
    ("Jane Doe", "Jane Doe"),
    ("", ""),
])
def test_fec_names_shown_as_written(filed, shown):
    assert person_display_name(filed) == shown


def test_mc_without_the_table(monkeypatch):
    monkeypatch.setattr(name_casing, "_cache", {})
    assert person_display_name("MCDONNELL, PAT") == "Pat McDonnell"
    assert person_display_name("MACK, CONNIE") == "Connie Mack"


def test_the_api_serves_the_display_name_beside_the_filed_one():
    cand = Candidate(id="H6TX12000", name="GOLDMAN, CRAIG", ballot_name=None, party="REP",
                     confirmed_general=True, has_raised_funds=True)
    out = _candidate_summary(cand)
    assert out["name"] == "GOLDMAN, CRAIG"  # the filing, untouched
    assert out["ballotName"] is None
    assert out["displayName"] == "Craig Goldman"


def test_a_sitting_member_takes_the_congress_gov_name(db_session):
    # An FEC row filed with the names swapped: the crosswalk's member name
    # is the right one, and keeps its own casing.
    db_session.add(Representative(id="nick-lalota", bioguide_id="L000598", name="Nick LaLota",
                                  state="NY", district=1, party="R", is_current=True))
    cand = Candidate(id="H2NY01000", race_id="2026-HOUSE-NY-1", name="NICK, LALOTA", ballot_name=None, party="REP",
                     member_bioguide="L000598")
    db_session.add(cand)
    db_session.commit()
    assert _candidate_summary(cand)["displayName"] == "Nick LaLota"
    # A printed ballot name still wins, verbatim.
    cand.ballot_name = "NICK LALOTA"
    assert _candidate_summary(cand)["displayName"] == "NICK LALOTA"


def test_a_printed_ballot_name_is_verbatim():
    # §7: the state's printing is shown as printed, capitals included;
    # only a candidate no state has printed falls back to the FEC name.
    printed = SimpleNamespace(name="GOLDMAN, CRAIG", ballot_name="CRAIG GOLDMAN")
    assert candidate_display_name(printed) == "CRAIG GOLDMAN"
    filed_only = SimpleNamespace(name="GOLDMAN, CRAIG", ballot_name=None)
    assert candidate_display_name(filed_only) == "Craig Goldman"
