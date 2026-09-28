"""Tests for Vermont's ballot-measure strategy (ballot_measures_vt.py).

fixtures_vt_2026_notice.json is REAL — page.extract_text() output for
both pages of the General Assembly's 2026 constitutional-amendment
notice ("2026-Vt.-Const.-amendment-publication-notice-for-Leg.-website-
ID-421676.pdf"), fetched live 2026-09-28 from legislature.vermont.gov.
Proposal 4's amendment text runs across the page break, where the real
"(ID 421676)" footer sits.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.pipeline.fetch import ballot_measures_pdf
from app.pipeline.fetch import ballot_measures_vt as vt

FIXTURE = json.loads((Path(__file__).parent / "fixtures_vt_2026_notice.json").read_text())


def _pages(texts):
    return [SimpleNamespace(extract_text=lambda t=t: t) for t in texts]


def test_reads_both_real_2026_proposals():
    results = vt.parse_document(_pages(FIXTURE))
    assert [r["number"] for r in results] == ["3", "4"]
    three, four = results
    assert three["title"] == "Proposal 3"
    assert three["official_summary"] == (
        "Proposal 3 would amend the Vermont Constitution to provide that the "
        "citizens of the State have a right to collectively bargain."
    )
    assert four["official_summary"].startswith(
        "Proposal 4 would amend the Vermont Constitution to specify that the government"
    )
    assert four["official_summary"].endswith("gender expression, or national origin.")


def test_amendment_text_and_footer_never_leak_into_the_summary():
    for r in vt.parse_document(_pages(FIXTURE)):
        assert "Article 23" not in r["official_summary"]
        assert "(ID" not in r["official_summary"]


def test_no_yes_no_or_fiscal_text_is_invented():
    for r in vt.parse_document(_pages(FIXTURE)):
        assert r["yes_means"] is None and r["no_means"] is None
        assert r["fiscal_impact"] is None and r["fiscal_authority"] is None
        assert r["origin"] == r["title_authority"] == "Vermont General Assembly"


def test_a_notice_with_no_recognisable_proposal_raises_rather_than_reading_as_none():
    # [] would be recorded as CONFIRMED_NONE; this notice only exists
    # because proposals are going to the voters.
    with pytest.raises(ValueError):
        vt.parse_document(_pages(["STATE OF VERMONT\nGENERAL ASSEMBLY\nSomething else entirely."]))


def test_registered_as_a_single_pdf_strategy():
    assert ballot_measures_pdf.STRATEGIES["vt_constitutional_amendment_notice"] is vt.parse_document
    assert ballot_measures_pdf.is_configured("VT")
