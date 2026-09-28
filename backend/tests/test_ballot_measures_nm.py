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


def test_four_amendments_with_ballot_text_and_lcs_summary():
    measures = nm.parse_amendments(AMEND)
    assert [m["number"] for m in measures] == ["1", "2", "3", "4"]
    one = measures[0]
    assert one["title"] == "Constitutional Amendment 1"
    assert one["official_summary"].startswith(
        "PROPOSING AN AMENDMENT TO ARTICLE 4, SECTION 22 OF THE CONSTITUTION OF NEW MEXICO"
    )
    assert "SUMMARY of Proposed Constitutional Amendment 1: In 2025, the New Mexico Legislature" in one["official_summary"]
    assert one["official_summary"].endswith("a written, substantive explanation for the veto.")
    # Page footers never leak into a summary.
    for m in measures:
        assert "SUMMARY OF AND ARGUMENTS FOR AND AGAINST" not in m["official_summary"]
        assert "BACKGROUND" not in m["official_summary"]
        assert m["yes_means"] is None and m["no_means"] is None


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
