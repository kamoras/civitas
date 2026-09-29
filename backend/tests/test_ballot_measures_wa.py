"""Tests for Washington's strategy (ballot_measures_wa.py).

Both fixtures are REAL, fetched 2026-09-28:
- fixtures_wa_proposed_measures_2026.html: the <h2>2026</h2> section of
  sos.wa.gov/elections/voters/proposed-ballot-measure-information (the
  certification link and each measure's document links);
- fixtures_wa_measure_documents_2026.json: extract_text() of each
  measure's Attorney General ballot-title letter and page 1 of its OFM
  Fiscal Impact Statement — including the font's unmapped "ff" glyph
  as pdfplumber emits it ("(cid:431)").
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_wa as wa

HERE = Path(__file__).parent
PAGE = (HERE / "fixtures_wa_proposed_measures_2026.html").read_text()
DOCS = json.loads((HERE / "fixtures_wa_measure_documents_2026.json").read_text())


def test_certified_section_lists_the_three_real_measures():
    measures = wa.measure_links(PAGE, 2026)
    assert [m[1] for m in measures] == ["IL26-001", "IL26-638", "IP26-645"]
    assert all("ballot title" in " ".join(links) for _, _, links in measures)


def test_section_without_certification_link_is_not_a_list():
    uncertified = PAGE.replace("Certification of Measures", "Draft list")
    assert wa.measure_links(uncertified, 2026) is None
    assert wa.measure_links(PAGE, 2027) is None


def test_ballot_title_and_summary_are_verbatim():
    m = wa.parse_ballot_title_letter(DOCS["IP26-645"]["ballot_title_letter"], "IP26-645", "Initiative No. IP26-645")
    assert m["title"] == "Initiative Measure No. IP26-645 concerns state and local taxes."
    assert m["official_summary"].startswith("Statement of Subject: Initiative Measure No. IP26-645")
    assert "Should this measure be enacted into law? Ballot Measure Summary: This measure would repeal" in m["official_summary"]
    assert "[ ]" not in m["official_summary"]
    assert m["origin"] == "Washington voters (initiative to the people)"
    assert m["yes_means"] is None and m["no_means"] is None


def test_line_break_hyphen_is_rejoined():
    m = wa.parse_ballot_title_letter(DOCS["IL26-001"]["ballot_title_letter"], "IL26-001", "h")
    assert "public-school children, including" in m["official_summary"]
    assert m["origin"] == "Washington voters (initiative to the Legislature)"


def test_fiscal_summary_restores_the_ff_glyph_only():
    fiscal = wa.parse_fiscal_summary(DOCS["IL26-638"]["fiscal_impact_page1"])
    assert "The Office of the Superintendent of Public Instruction" in fiscal
    assert "(cid:" not in fiscal
    assert fiscal.endswith("but this fiscal impact is indeterminate.")
    garbled = DOCS["IL26-638"]["fiscal_impact_page1"].replace("minor costs", "minor (cid:99)osts")
    assert wa.parse_fiscal_summary(garbled) is None


@pytest.mark.asyncio
async def test_fetch_joins_letter_and_fiscal_and_fails_closed(monkeypatch):
    async def fake_page(*a, **k):
        return PAGE

    async def fake_pdf_text(client, url, label, pages=None):
        mid = next(m for m in DOCS if m[-3:] in url)
        key = "fiscal_impact_page1" if "FIS" in url else "ballot_title_letter"
        return DOCS[mid][key]

    monkeypatch.setattr(wa, "fetch_text_with_retry", fake_page)
    monkeypatch.setattr(wa, "_pdf_text", fake_pdf_text)
    pairs = await wa.fetch_measures(None, 2026)
    assert [p["number"] for p, _ in pairs] == ["IL26-001", "IL26-638", "IP26-645"]
    assert all(p["fiscal_impact"] and p["fiscal_authority"] == wa.FISCAL_AUTHORITY for p, _ in pairs)

    async def missing_letter(client, url, label, pages=None):
        return None if "638" in url and "FIS" not in url else await fake_pdf_text(client, url, label, pages)

    monkeypatch.setattr(wa, "_pdf_text", missing_letter)
    assert await wa.fetch_measures(None, 2026) is None

    async def garbled_fiscal(client, url, label, pages=None):
        """The regression: a linked fiscal statement that didn't parse was
        dropped and the measure published as if it had none."""
        text = await fake_pdf_text(client, url, label, pages)
        return text.replace("minor costs", "minor (cid:99)osts") if "FIS" in url and "638" in url else text

    monkeypatch.setattr(wa, "_pdf_text", garbled_fiscal)
    assert await wa.fetch_measures(None, 2026) is None


def test_documents_under_an_unrecognised_heading_fail_the_list():
    odd = PAGE.replace("<h3><strong>Initiative No. IL26-638</strong></h3>", "<h3><strong>Mystery measure</strong></h3>")
    assert wa.measure_links(odd, 2026) is None
