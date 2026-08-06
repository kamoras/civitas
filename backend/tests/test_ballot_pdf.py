"""Tests for direct ballot-PDF parsing.

The fixture text below is REAL — extracted (via pdfplumber, column-cropped)
from Somerville MA's actual 2026-09-01 state primary ballot PDF, not
synthesized. Parser bugs found and fixed against this exact text during
development: a "tolerate one stray leading character" rule that silently
ate the first real letter of every candidate name, a required trailing
"+" that excluded the one uncontested candidate (no ovals printed), and
page-title fragments ("STATE PRIMARY", "Y SOMERVILLE") bleeding into the
first office name. All three are covered below so they can't regress.
"""

import json
import time

import pytest

from app.api import elections
from app.pipeline.fetch import ballot_pdf


def _body(response):
    return json.loads(response.body)

# One full column (of three) from the real PDF, including the page's
# full-width title/instructions bleeding into the top of the crop, and a
# genuinely uncontested candidate (Attorney General) with no "+" marks.
REAL_COLUMN_TEXT = """\
The Com
Tu
To vote for a candidate, fill in the ova
ballot, write the person's name and r
SENATOR IN CONGRESS
Vote for not more than ONE
EDWARD J. MARKEY 360 Charles St., Malden + + + + +
United States Senator
SETH MOULTON 37 Chestnut St., Salem + + + + + + + + + +
Member of Congress; Veteran
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
GOVERNOR
Vote for not more than ONE
MAURA HEALEY 4 Cheswick Rd., Arlington + + + + + + + +
Governor; Former Attorney General
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
LIEUTENANT GOVERNOR
Vote for not more than ONE
KIMBERLEY DRISCOLL 16 Glenn Ave., Salem + + + + +
Lieutenant Governor; Former Mayor of Salem
DO NOT VOTE IN THIS SPACE. a
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
ATTORNEY GENERSAL
Vote for not more than ONE
ANDREA JOY CAMPBELL 59 William Bradford Rd., Dartmouth
Attorney General
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
SECRETARY OF STATE
Vote for not more than ONE
WILLIAM FRANCIS GALVIN 46 Lake St., Boston + +
Present Secretary; Candidate for Re-nomination
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
TREASURER
Vote for not more than ONE
DEBORAH B. GOLDBERG 37 Hyslop Rd., Brookline +
Present Treasurer; Candidate for Re-nomination
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
"""

# A second real column: multi-candidate race (5 candidates, one race) and
# a district qualifier that spans two lines before "Vote for not more
# than ONE" ("REPRESENTATIVE IN GENERAL COURT" / "TWENTY-SIXTH MIDDLESEX
# DISTRICT"). Also includes "REPRESENTATIVE IN CONGRESpS" — the real PDF's
# own text-extraction artifact (a stray lowercase "p" breaking the
# all-caps office line) — verifying that office is safely dropped rather
# than guessed at, not silently "fixed".
REAL_COLUMN_TEXT_MULTI_CANDIDATE = """\
mmonwealth of Mass
STATE PRIMARY
DEMOCRATIC PARTY
OFFICIAL
EARLY/ABSENTEE
BALLOT
uesday, September 1, 20
al to the right of the candidate's n
residence in the blank space provided
AUDITOR
Vote for not more than ONE
DIANA DiZOGLIO 30 Olive St., Methuen + + + + + + + + + +
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
COUNCILLOR
SIXTH DISTRICT Vote for not more than ONE
TERRENCE W. KENNEDY 3 Stafford Rd., Lynnfield + +
Present Governor's Councillor; Candidate for Re-nomination
DIANN MARY BAYLIS 39 Ticehurst Ln., Marblehead + +
a DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
l
REPRESENTATIVE IN CONGRESpS
SEVENTH DISTRICT Vote for not more than ONE
AYANNA S. PRESSLEY 119 Blake St., Boston + + + + +
U.S. Representative; Former Boston City Councillor at-Large
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
m
WRITE-IN SPACE ONLY
SENATOR IN GENERAL COURT
SECOND MIDDLESEX DISTRICT Vote for not more than ONE
BURHAN AZEEM 19 Woodbridge St., Cambridge + + + + + +
Vice Mayor of Cambridge
CHRISTINE P. BARBER 73 Newbury St., Somerville + +
Current State Representative
THOMAS E. HOPCROFT 99 Pond St., Winchester + + +
School Committee Member
MATTHEW McLAUGHLIN 28 Mount Vernon St., Somerville
Somerville City Councillor; Veteran
ERIKA UYTERHOEVEN 11 Wesley Park, Somerville + +
Current State Representative
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
REPRESENTATIVE IN GENERAL COURT
TWENTY-SIXTH MIDDLESEX
DISTRICT Vote for not more than ONE
MIKE CONNOLLY 4 Ashburton Pl., Cambridge + + + + + + +
Candidate for Re-nomination
NEIL S. MILLER 425 Massachusetts Ave., Cambridge + + +
DO NOT VOTE IN THIS SPACE.
USE BLANK LINE BELOW FOR WRITE-IN.
WRITE-IN SPACE ONLY
"""


def test_parses_all_offices_with_full_names():
    contests = ballot_pdf._parse_column(REAL_COLUMN_TEXT)
    offices = {c["office"]: c["candidates"] for c in contests}
    assert set(offices) == {
        "SENATOR IN CONGRESS", "GOVERNOR", "LIEUTENANT GOVERNOR",
        "ATTORNEY GENERSAL", "SECRETARY OF STATE", "TREASURER",
    }
    # The bug this regresses: an earlier "tolerate a stray leading
    # character" rule silently ate the first real letter of every name.
    names = {c["name"] for cands in offices.values() for c in cands}
    assert "EDWARD J. MARKEY" in names
    assert "DWARD J. MARKEY" not in names


def test_uncontested_candidate_has_no_plus_marks_and_still_parses():
    """Attorney General is uncontested on the real ballot and prints no
    "+" oval marks at all — the regex must not require them."""
    contests = ballot_pdf._parse_column(REAL_COLUMN_TEXT)
    ag = next(c for c in contests if c["office"] == "ATTORNEY GENERSAL")
    assert ag["candidates"] == [
        {"name": "ANDREA JOY CAMPBELL", "address": "59 William Bradford Rd., Dartmouth"},
    ]


def test_page_title_fragments_do_not_bleed_into_first_office():
    """"STATE PRIMARY" and other page-title fragments are all-caps and
    would otherwise get glued onto the first real office name."""
    contests = ballot_pdf._parse_column(REAL_COLUMN_TEXT_MULTI_CANDIDATE)
    offices = [c["office"] for c in contests]
    assert "AUDITOR" in offices
    assert not any("STATE PRIMARY" in o for o in offices)


def test_multi_candidate_race_captures_every_candidate():
    contests = ballot_pdf._parse_column(REAL_COLUMN_TEXT_MULTI_CANDIDATE)
    senate = next(
        c for c in contests if c["office"] == "SENATOR IN GENERAL COURT SECOND MIDDLESEX DISTRICT"
    )
    names = [c["name"] for c in senate["candidates"]]
    assert names == [
        "BURHAN AZEEM", "CHRISTINE P. BARBER", "THOMAS E. HOPCROFT",
        "MATTHEW McLAUGHLIN", "ERIKA UYTERHOEVEN",
    ]


def test_corrupted_office_line_is_never_silently_corrected():
    """"REPRESENTATIVE IN CONGRESpS" is the real PDF's own extraction
    artifact (a stray lowercase "p" breaking the all-caps office match).
    The parser must never silently "fix" it to CONGRESS — that would be
    inventing text, not reading it. What it actually does, verified: the
    corrupted line itself is dropped, but the next line's district
    qualifier ("SEVENTH DISTRICT Vote for not more than ONE") is still
    genuinely all-caps and gets kept as a real, if incomplete, office
    label — so Ayanna Pressley's contest is NOT lost, just under a
    shorter name than the full office title. Incomplete-but-true beats
    both "wrong" and "silently dropped" here."""
    contests = ballot_pdf._parse_column(REAL_COLUMN_TEXT_MULTI_CANDIDATE)
    offices = [c["office"] for c in contests]
    assert not any("CONGRESS" in o or "CONGRESpS" in o for o in offices)
    pressley = next(
        c for c in contests
        for cand in c["candidates"] if cand["name"] == "AYANNA S. PRESSLEY"
    )
    assert pressley["office"] == "SEVENTH DISTRICT"


def test_multi_line_district_qualifier_folds_into_office_name():
    contests = ballot_pdf._parse_column(REAL_COLUMN_TEXT_MULTI_CANDIDATE)
    offices = [c["office"] for c in contests]
    assert "SENATOR IN GENERAL COURT SECOND MIDDLESEX DISTRICT" in offices


def test_parse_column_empty_on_no_offices():
    assert ballot_pdf._parse_column("just some prose, no ballot content") == []


def test_column_bounds_scale_to_page_width():
    bounds = ballot_pdf._column_bounds_px(648.0, [[0.0, 0.5], [0.5, 1.0]])
    assert bounds == [(0.0, 324.0), (324.0, 648.0)]


@pytest.mark.asyncio
async def test_town_ballot_discloses_which_election_the_pdf_is_for(monkeypatch, db_session):
    """A PDF-sourced ballot can be for a DIFFERENT election than the page's
    own "GENERAL ELECTION" header — right now, Somerville's only
    published PDF is its September primary, not the November general.
    Showing those candidates with no disclosure would be actively
    misleading, not just incomplete — election_name/election_date from
    ballot_pdf_sources.json must reach the API response."""
    async def fake_fetch(client, db, town):
        return {"contests": [{"office": "GOVERNOR", "candidates": [{"name": "TEST CANDIDATE"}]}],
                "sourceUrl": "https://example.com/ballot.pdf"}

    monkeypatch.setattr(ballot_pdf, "fetch_town_ballot_pdf", fake_fetch)
    data = _body(await elections.town_ballot("MA", "Somerville", db=db_session))
    assert data["status"] == "covered"
    assert data["electionName"] == "2026 Massachusetts State Primary (Democratic Party)"
    assert data["electionDate"] == "2026-09-01"


def test_candidate_regex_is_not_vulnerable_to_catastrophic_backtracking():
    """CodeQL flagged an earlier version of _CANDIDATE_RE: the name group
    had two overlapping ways to match a "LETTER." token (an explicit
    `[A-Z]\\.` branch, and separately via `[A-Z][A-Za-z.'-]*` since "."
    is already in that character class), and the ambiguity was
    exponential-backtracking-prone on adversarial input. A many-token
    string that never reaches a valid address/trailing-+ shape used to
    hang; it must now fail fast."""
    evil_input = "A. " * 40 + "X"
    start = time.monotonic()
    result = ballot_pdf._CANDIDATE_RE.match(evil_input)
    elapsed = time.monotonic() - start
    assert result is None
    assert elapsed < 1.0, f"regex took {elapsed:.2f}s — catastrophic backtracking regressed"
