"""State offices from Missouri's certified November ballot
(state_candidates_certified_pdf.py).

fixtures_mo_certification_2026.txt is REAL: the text lines pdfplumber
extracts from the Secretary of State's "Certification of Candidates and
Party Emblems" for the 2026-11-03 general election, certified 2026-08-25
and linked from sos.mo.gov/elections/results (fetched 2026-09-28). It
is exactly what parse_certification reads in production, kept whole
because the prose after the last party section (the certification
text, the judicial candidates) is what must NOT be read as candidates.
"""

from pathlib import Path

from app.pipeline.fetch.state_candidates_certified_pdf import parse_certification

_LINES = (Path(__file__).parent / "fixtures_mo_certification_2026.txt").read_text().splitlines()


def _state():
    return [r for r in parse_certification(_LINES, state_offices=True) if r["office"] not in ("S", "H")]


def test_without_the_opt_in_only_federal_candidates_are_read():
    records = parse_certification(_LINES)
    assert {r["office"] for r in records} == {"H"}
    # 8 districts x R/D/L, as the source entry's description says.
    assert len(records) == 24


def test_the_opt_in_leaves_the_federal_candidates_unchanged():
    federal = [r for r in parse_certification(_LINES, state_offices=True) if r["office"] in ("S", "H")]
    assert federal == parse_certification(_LINES)


def test_state_auditor_lists_every_certified_candidate():
    """The only executive office on Missouri's 2026 ballot. A certified
    ballot, so each party's one candidate is a record — there is no
    winner to resolve, and the Libertarian is as much on the ballot as
    the two major-party candidates."""
    auditor = {(r["party"], r["last_name"], r["district"]) for r in _state() if r["office"] == "auditor"}
    assert auditor == {
        ("R", "Scott Fitzpatrick", None),
        ("D", "Quentin Wilson", None),
        ("L", "Dustin Coffell", None),
    }


def test_no_other_executive_office_is_invented():
    assert {r["office"] for r in _state()} == {"auditor", "upper", "lower"}


def test_every_legislative_candidate_is_read_under_its_party():
    state = _state()
    upper = [r for r in state if r["office"] == "upper"]
    lower = [r for r in state if r["office"] == "lower"]
    # Counted off the document's own "District N, Name" lines:
    # R 16 / D 17 / L 1 for the Senate, R 134 / D 158 / L 11 / I 2 for
    # the House.
    assert len(upper) == 34
    assert len(lower) == 305
    assert len({r["district"] for r in upper}) == 17  # the even-numbered half
    assert len({r["district"] for r in lower}) == 163
    senate_30 = {(r["party"], r["last_name"]) for r in upper if r["district"] == "30"}
    assert senate_30 == {("R", "Melanie Stinnett"), ("D", "Betsy Fogle"), ("L", "Cecil Ince")}


def test_independents_are_ordinary_ballot_entries():
    independents = {(r["district"], r["last_name"]) for r in _state() if r["party"] == "I"}
    assert independents == {("127", "Racheal Martin"), ("128", "Shane Sawyer")}


def test_partisan_circuit_judges_are_not_read():
    """"For Circuit Judge" sits inside the Republican and Democratic
    sections ("Circuit 11 Division 1, Brittney R. Smith"); judicial is a
    separate claim this strategy does not make."""
    names = {r["last_name"] for r in _state()}
    assert "Brittney R. Smith" not in names
    assert "Josh Devine" not in names


def test_a_page_break_inside_a_list_does_not_end_it():
    # The Republican State Senate list breaks across a page after
    # District 28; District 30's candidate is on the next page.
    upper_r = {r["district"] for r in _state() if r["office"] == "upper" and r["party"] == "R"}
    assert {"28", "30", "32", "34"} <= upper_r


def test_the_prose_after_the_last_section_is_never_a_candidate():
    names = {r["last_name"] for r in _state()}
    assert "CERTIFICATION" not in names
    assert "Denny Hoskins, CPA" not in names
    assert "Paul C. Wilson" not in names  # a Supreme Court retention candidate


async def test_the_opt_in_reaches_the_parser(monkeypatch):
    from app.pipeline.fetch import state_candidates_certified_pdf as cp

    async def fake_link(*a, **kw):
        return "https://www.sos.mo.gov/x/2026GeneralElectionCertifiedCandidates.pdf"

    async def fake_bytes(*a, **kw):
        return b"%PDF"

    monkeypatch.setattr(cp, "discover_certification_link", fake_link)
    monkeypatch.setattr(cp, "fetch_bytes_with_retry", fake_bytes)
    monkeypatch.setattr(cp, "_lines", lambda content: _LINES)
    source = {"page_url": "https://www.sos.mo.gov/elections/results", "link_regex": "x"}
    federal_only = await cp.fetch_confirmed_candidates(None, 2026, "MO", source)
    both = await cp.fetch_confirmed_candidates(None, 2026, "MO", {**source, "statewide_offices": True})
    assert len(federal_only) == 24
    assert len(both) == 24 + 3 + 34 + 305
