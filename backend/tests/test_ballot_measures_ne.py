"""Tests for Nebraska's ballot-measure strategy (ballot_measures_ne.py).

fixtures_ne_ballot_measures.json is REAL — sos.nebraska.gov/elections'
2026 general-election ballot-measure block, the Informational
Pamphlet's cover, contents and each initiative's Ballot Title and Text
page (extract_text()), the LR19CA document links from the Legislature's
bill page, and extract_text() of the LR19CA Slip Law, fetched live
2026-09-28.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ne as ne

FIXTURE = json.loads((Path(__file__).parent / "fixtures_ne_ballot_measures.json").read_text())


def test_elections_page_lists_initiatives_and_the_amendment():
    listing = ne.read_elections_page(FIXTURE["elections_page"], 2026)
    assert listing["initiatives"] == {"440", "441", "442"}
    assert listing["pamphlet"].endswith("/2026/2026%20Ballot%20Measures%20Pamphlet.pdf")
    assert listing["amendments"] == [
        "https://nebraskalegislature.gov/bills/view_bill.php?DocumentID=59458&docnum=LR19CA&leg=109",
    ]
    assert ne.read_elections_page(FIXTURE["elections_page"], 2028) is None


def test_pamphlet_initiatives_verbatim():
    pamphlet = ne.parse_pamphlet(FIXTURE["pamphlet_pages"])
    assert sorted(pamphlet) == ["440", "441", "442"]
    m = pamphlet["441"]
    assert m["title"] == "Initiative Measure 441"
    assert m["origin"] == "Nebraska voters (initiative petition)"
    assert m["yes_means"].startswith("A vote “FOR” will enact statutes allowing persons located within Nebraska")
    assert m["no_means"] == "A vote “AGAINST” means no such statutes will be enacted."
    assert m["official_summary"].startswith("Shall statutes be enacted that: (1) Allow persons")
    assert m["official_summary"].endswith("(2) Regulate such sports wagers?")
    # A question that runs past its "?" keeps its trailing sentence.
    assert pamphlet["442"]["official_summary"].endswith("compete against public schools or institutions.")
    assert "Internet-based" in pamphlet["440"]["official_summary"]
    for m in pamphlet.values():
        # Neb. Rev. Stat. §32-1410: the Attorney General writes both the
        # ballot title and the FOR/AGAINST statement of a petition measure.
        assert m["title_authority"] == ne.PETITION_TITLE_AUTHORITY
        assert m["framing_authority"] == ne.PETITION_FRAMING_AUTHORITY


def test_slip_law_link_and_ballot_language():
    url = ne.slip_law_url(FIXTURE["bill_page"], "https://nebraskalegislature.gov/bills/view_bill.php?DocumentID=59458")
    assert url == "https://nebraskalegislature.gov/FloorDocs/109/PDF/Slip/LR19CA.pdf"
    m = ne.parse_slip_law(FIXTURE["slip_law"], 2026)
    assert m["number"] == "LR19CA"
    assert m["title"] == "Legislative Resolution 19CA"
    assert m["official_summary"] == (
        "A constitutional amendment to change the limit on legislative terms from two "
        "consecutive terms to three consecutive terms."
    )
    assert m["title_authority"] == "Nebraska Legislature"
    assert m["yes_means"] is None  # lives only in the scanned ballot statement


def test_slip_law_for_another_election_is_refused():
    assert ne.parse_slip_law(FIXTURE["slip_law"], 2028) is None


async def test_pamphlet_short_of_the_page_range_refuses(monkeypatch):
    short_pages = [p for p in FIXTURE["pamphlet_pages"] if "Initiative Measure 442\nProposed" not in p]

    async def fake_text(client, limiter, url, label, **kw):
        return FIXTURE["elections_page"]

    async def fake_bytes(client, limiter, url, label, **kw):
        return b"%PDF"

    monkeypatch.setattr(ne, "fetch_text_with_retry", fake_text)
    monkeypatch.setattr(ne, "fetch_bytes_with_retry", fake_bytes)
    monkeypatch.setattr(ne, "_pdf_pages", lambda raw: short_pages)
    assert await ne.fetch_measures(None, 2026) is None


@pytest.mark.parametrize("old, new", [
    # The regression: the two kinds were found by independent matches, so
    # an initiative heading whose range didn't parse left the LR amendment
    # to be published alone — as the whole ballot.
    pytest.param("Initiative Nos. 440-442", "Initiatives 440 through 442",
                 id="unreadable_initiative_heading_never_publishes_the_amendment_alone"),
    pytest.param(">LR19CA</a>", ">Legislative Resolution 19CA</a>",
                 id="amendment_heading_without_its_lr_link_never_publishes_initiatives_alone"),
    pytest.param("</body>", "<p><strong>Referendum petition certified for the 2026 General Election</strong></p></body>",
                 id="unrecognised_measure_paragraph_for_the_year"),
])
def test_a_measure_listing_it_cannot_read_whole_refuses(old, new):
    page = FIXTURE["elections_page"].replace(old, new)
    assert page != FIXTURE["elections_page"]
    assert ne.read_elections_page(page, 2026) is None
