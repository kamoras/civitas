"""Tests for New Mexico's strategy (ballot_measures_nm.py).

fixtures_nm_2026.json is REAL — extract_text() of, fetched 2026-09-28:
- amendments_pages: pages 5, 6, 10, 14, 22 and 27 of nmlegis.gov's
  Constitutional_Amendments_2026.pdf (the "(ballot text)" page, the
  General Information page that ends it, and each amendment's
  "▸ SUMMARY" page — page footers included, as printed);
- bond_act_pages: PDF pages 23-25 of the FINAL version of HB 248 (2026),
  the 2026 Capital Projects General Obligation Bond Act, with the
  legislature's line numbers and running committee tag as printed.
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_nm as nm

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nm_2026.json").read_text())
AMEND = list(FIXTURE["amendments_pages"].values())
BOND = list(FIXTURE["bond_act_pages"].values())


def test_four_amendments_with_their_ballot_text_as_the_official_title():
    measures = nm.parse_amendments(AMEND)
    assert [m["number"] for m in measures] == ["1", "2", "3", "4"]
    one = measures[0]
    assert one["title"] == "Constitutional Amendment 1"
    assert one["official_title"].startswith(
        "PROPOSING AN AMENDMENT TO ARTICLE 4, SECTION 22 OF THE CONSTITUTION OF NEW MEXICO"
    )
    assert one["official_title"].endswith("WILL BECOME LAW.")
    for m in measures:
        # One drafter per quote: the Legislature's ballot text, never
        # joined with the Legislative Council Service's summary under a
        # shared attribution.
        assert m["title_authority"] == "New Mexico Legislature"
        assert m["official_summary"] is None
        assert "SUMMARY of Proposed" not in m["official_title"]
        assert "SUMMARY OF AND ARGUMENTS FOR AND AGAINST" not in m["official_title"]
        assert m["yes_means"] is None and m["no_means"] is None


def test_an_item_line_in_another_shape_is_refused_not_folded_into_the_previous_one():
    """The regression: "Constitutional Amendment 3: <text>" on one line
    didn't match the item pattern, so #3's ballot text was appended to
    #2's and #3 vanished — and its summary section was never checked
    against a ballot-text item."""
    pages = [AMEND[0].replace("Constitutional Amendment 3:\n\"PROPOSING", "Constitutional Amendment 3: \"PROPOSING")] + AMEND[1:]
    assert pages[0] != AMEND[0]
    with pytest.raises(ValueError):
        nm.parse_amendments(pages)


def test_summary_sections_must_match_the_ballot_text_items():
    # A summary for an amendment with no ballot-text item.
    extra = AMEND + ["▸ SUMMARY of Proposed Constitutional Amendment 5\nSomething.\n▸ BACKGROUND AND INFORMATION"]
    with pytest.raises(ValueError):
        nm.parse_amendments(extra)
    # A summary heading in a shape the reader doesn't know.
    odd = AMEND[:-1] + [AMEND[-1].replace("▸ SUMMARY of Proposed Constitutional Amendment 4", "▸ SUMMARY of Amendment 4")]
    with pytest.raises(ValueError):
        nm.parse_amendments(odd)


def test_amendment_without_a_summary_section_fails():
    with pytest.raises(ValueError):
        nm.parse_amendments(AMEND[:2])


def test_bond_questions_come_from_the_final_act_with_line_numbers_stripped():
    bonds = nm.parse_bond_act(BOND, "HB 248")
    assert [b["number"] for b in bonds] == ["HB 248 (1)", "HB 248 (2)", "HB 248 (3)"]
    senior = bonds[0]
    assert senior["title"] == (
        "The 2026 Capital Projects General Obligation Bond Act authorizes the issuance and sale "
        "of senior citizen facility improvement, construction and equipment acquisition bonds."
    )
    # The FINAL act's amount — not the introduced bill's $30,000,000.
    assert "($21,582,213)" in senior["official_summary"]
    assert "three hundred fifty-two million two hundred twenty-five thousand" in bonds[2]["official_summary"]
    for b in bonds:
        assert b["official_summary"].endswith("as permitted by law?")
        assert "HTRC" not in b["official_summary"] and "For___" not in b["official_summary"]


def test_a_bond_item_the_question_pattern_misses_refuses_the_act():
    """findall only raised on ZERO questions; one item worded outside the
    pattern dropped out of the list silently."""
    reworded = [p.replace("Obligation Bond Act authorizes the issuance and sale of\n10 library", "Obligation Bond Act provides for the issuance and sale of\n10 library") for p in BOND]
    assert reworded != BOND
    with pytest.raises(ValueError):
        nm.parse_bond_act(reworded, "HB 248")


@pytest.mark.asyncio
async def test_no_configured_bond_act_is_a_failure_not_an_amendments_only_list(monkeypatch):
    monkeypatch.setattr(nm, "source_for_state", lambda st: {"bond_acts": {}})
    assert await nm.fetch_measures(None, 2026) is None


@pytest.mark.asyncio
async def test_fetch_returns_amendments_then_bonds(monkeypatch):
    monkeypatch.setattr(nm, "source_for_state", lambda st: {
        "bond_acts": {"2026": {"bill": "HB 248", "url": "https://example.test/HB0248.pdf"}},
    })

    async def fake_pages(client, url, label):
        return BOND if url.endswith("HB0248.pdf") else AMEND

    monkeypatch.setattr(nm, "_pdf_pages_text", fake_pages)
    pairs = await nm.fetch_measures(None, 2026)
    assert len(pairs) == 7
    assert pairs[0][1] == nm.AMENDMENTS_URL_PATTERN.format(year=2026)
    assert pairs[-1][1] == "https://example.test/HB0248.pdf"
