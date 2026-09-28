"""Tests for Montana's strategy (ballot_measures_mt.py).

Both fixtures are REAL, fetched 2026-09-28:
- fixtures_mt_proposed_ballot_issues_2026.html: sosmt.gov/elections/
  ballot_issues/proposed-2026-ballot-issues/, cut from the "Issues
  Qualified for the 2026 General Election Ballot" heading through the
  "Issues Not Qualified" heading — so the signature-gathering and
  submitted issues that must NOT be read are still in it.
- fixtures_mt_ballot_language_2026.json: page 1 extract_text() of each
  qualified issue's "BALLOT LANGUAGE FOR ..." PDF (CI-132, CI-133, I-194).
"""

import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_mt as mt

HERE = Path(__file__).parent
PAGE = (HERE / "fixtures_mt_proposed_ballot_issues_2026.html").read_text()
LANGUAGE = json.loads((HERE / "fixtures_mt_ballot_language_2026.json").read_text())


def test_only_the_qualified_section_is_read():
    issues = mt.qualified_issues(PAGE, 2026)
    assert [i for i, _ in issues] == ["CI-132", "CI-133", "I-194"]
    # CI-129 etc. are still gathering signatures on the same page.
    assert "CI-129" in PAGE


def test_missing_heading_is_none_not_an_empty_list():
    assert mt.qualified_issues(PAGE, 2028) is None


def test_ballot_language_is_verbatim_with_origin_read_from_the_statement():
    ci132 = mt.parse_ballot_language(LANGUAGE["CI-132"], "CI-132")
    assert ci132["title"] == "CONSTITUTIONAL INITIATIVE NO. 132"
    assert ci132["official_summary"] == (
        "CI-132 amends the Montana Constitution to require that judicial elections remain nonpartisan."
    )
    assert ci132["origin"] == "Montana voters (initiative petition)"
    # "[] YES on Constitutional Amendment CI-132" is a box label, not framing.
    assert ci132["yes_means"] is None and ci132["no_means"] is None
    i194 = mt.parse_ballot_language(LANGUAGE["I-194"], "I-194")
    assert i194["official_summary"].startswith("I-194, if passed, limits the powers of artificial persons")
    assert i194["official_summary"].endswith("political committees, or public corporations.")
    assert "YES on" not in i194["official_summary"]


def test_a_document_that_is_not_a_ballot_statement_is_rejected():
    assert mt.parse_ballot_language("THE COMPLETE TEXT OF INITIATIVE NO. 194", "I-194") is None


def _fake_network(monkeypatch, missing=None):
    by_file = {"72919": "CI-132", "73545": "CI-133", "73640": "I-194"}

    async def fake_text(*a, **k):
        return PAGE

    async def fake_resolve(client, href, base):
        issue = next(v for k, v in by_file.items() if k in href)
        return None if issue == missing else (issue.encode(), f"https://sosmt.gov/{issue}.pdf")

    monkeypatch.setattr(mt, "fetch_text_with_retry", fake_text)
    monkeypatch.setattr(mt, "_resolve_pdf", fake_resolve)
    monkeypatch.setattr(mt, "_first_page_text", lambda b: LANGUAGE[b.decode()])


@pytest.mark.asyncio
async def test_fetch_returns_every_qualified_issue_with_its_statement_url(monkeypatch):
    _fake_network(monkeypatch)
    pairs = await mt.fetch_measures(None, 2026)
    assert [(p["number"], url) for p, url in pairs] == [
        ("CI-132", "https://sosmt.gov/CI-132.pdf"),
        ("CI-133", "https://sosmt.gov/CI-133.pdf"),
        ("I-194", "https://sosmt.gov/I-194.pdf"),
    ]


@pytest.mark.asyncio
async def test_one_unreachable_statement_fails_the_whole_state(monkeypatch):
    _fake_network(monkeypatch, missing="CI-133")
    assert await mt.fetch_measures(None, 2026) is None


def test_entry_without_a_recognised_issue_link_fails_the_list():
    odd = PAGE.replace(">CI-133<", ">Constitutional Initiative 133<")
    assert mt.qualified_issues(odd, 2026) is None
