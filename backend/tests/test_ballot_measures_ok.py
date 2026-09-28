"""Tests for Oklahoma's ballot-measure strategy (ballot_measures_ok.py).

fixtures_ok_state_questions.json is REAL — the header row and SQ
844-848 rows of the Secretary of State's State Questions register,
fetched live 2026-09-28.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measures_ok as ok
from app.pipeline.fetch.ballot_measure_text import NotYetPublished

REGISTER = json.loads((Path(__file__).parent / "fixtures_ok_state_questions.json").read_text())["register"]


def test_only_the_two_november_2026_questions():
    rows = ok.parse_register(REGISTER, 2026)
    assert [(m["number"], m["title"]) for m, _ in rows] == [
        ("847", "Real Property Valuation"),
        ("845", "Judicial Nominating Commission"),
    ]
    # SQ 846 (August 25, 2026) and 848 (April 6, 2027) are other elections.
    assert rows[0][1] == "https://www.sos.ok.gov/documents/questions/847.pdf"


def test_no_ocr_text_is_presented_as_ballot_language():
    for m, _ in ok.parse_register(REGISTER, 2026):
        assert m["official_title"] is None
        assert m["official_summary"] is None
        assert m["yes_means"] is None and m["no_means"] is None
        assert m["origin"] == "Oklahoma Legislature"


def test_year_with_no_row_is_not_yet_published_never_none_or_empty(monkeypatch):
    """The register isn't a certified list, so no row for the election
    can't be "none" — and it isn't a failure (it used to be None, an
    ingest-failure page every night of a year with no State Question)."""
    assert ok.parse_register(REGISTER, 2030) == []

    async def get_text(*a, **kw):
        return REGISTER

    monkeypatch.setattr(ok, "fetch_text_with_retry", get_text)
    with pytest.raises(NotYetPublished):
        asyncio.run(ok.fetch_measures(None, 2030))
    assert len(asyncio.run(ok.fetch_measures(None, 2026))) == 2


def test_an_election_date_in_another_form_refuses_the_register():
    """A row dated this election in a form the date pattern doesn't know
    used to be skipped as undated — dropping a real State Question."""
    reworded = REGISTER.replace("ELECTION DATE:  November 3, 2026", "ELECTION DATE: Nov. 3, 2026", 1)
    assert reworded != REGISTER
    assert ok.parse_register(reworded, 2026) is None


def test_general_election_label_follows_the_statutory_rule():
    assert ok._general_election_label(2026) == "November 3, 2026"
    assert ok._general_election_label(2028) == "November 7, 2028"


PAGES = json.loads((Path(__file__).parent / "fixtures_ok_state_questions.json").read_text())


def _without_rows(page_html, keep):
    """The real register page with every numbered row not in `keep` cut."""
    from lxml import html as lxml_html

    tree = lxml_html.fromstring(page_html)
    for tr in tree.xpath("//tr[td]"):
        first = " ".join(tr.xpath("td")[0].text_content().split()) if tr.xpath("td") else ""
        if first.isdigit() and first not in keep:
            tr.getparent().remove(tr)
    return lxml_html.tostring(tree, encoding="unicode")


def _serve_pages(monkeypatch, first, following):
    posts = []

    async def get_text(*a, **kw):
        return first

    async def post(client, limiter, method, url, **kw):
        from types import SimpleNamespace

        posts.append(kw["data"]["__EVENTARGUMENT"])
        return SimpleNamespace(text=following[len(posts) - 1]) if len(posts) <= len(following) else None

    monkeypatch.setattr(ok, "fetch_text_with_retry", get_text)
    monkeypatch.setattr(ok, "fetch_with_retry", post)
    return posts


def test_the_real_first_page_already_reaches_an_earlier_election(monkeypatch):
    posts = _serve_pages(monkeypatch, PAGES["page_1"], [])
    assert [m["number"] for m, _ in asyncio.run(ok.fetch_measures(None, 2026))] == ["847", "845"]
    assert posts == []


def test_november_rows_pushed_to_page_two_are_still_read(monkeypatch):
    """The regression: only page 1 was read. Filings after SQ 848 push
    November's 845 and 847 onto page 2; the reader published a one-item
    list (or "not yet") without a sign anything was missing."""
    page_1 = _without_rows(PAGES["page_1"], {"848"})
    page_2 = _without_rows(PAGES["page_1"], {str(n) for n in range(834, 848)})
    assert not ok.reaches_before(page_1, 2026)
    posts = _serve_pages(monkeypatch, page_1, [page_2])
    assert [m["number"] for m, _ in asyncio.run(ok.fetch_measures(None, 2026))] == ["847", "845"]
    assert posts == ["Page$2"]


def test_a_register_that_ends_or_fails_before_an_earlier_election_is_a_failure(monkeypatch):
    page_1 = _without_rows(PAGES["page_1"], {"848"})
    _serve_pages(monkeypatch, page_1, [])  # the Page$2 postback fails
    assert asyncio.run(ok.fetch_measures(None, 2026)) is None
    no_pager = page_1.replace("Page$", "Pager$")
    _serve_pages(monkeypatch, no_pager, [])
    assert asyncio.run(ok.fetch_measures(None, 2026)) is None
    _serve_pages(monkeypatch, page_1, [page_1] * ok.MAX_PAGES)
    assert asyncio.run(ok.fetch_measures(None, 2026)) is None


def test_a_row_dated_this_election_only_by_its_column_refuses(monkeypatch):
    reworded = PAGES["page_1"].replace("ELECTION DATE:  November 3, 2026", "Set for the general election", 1)
    assert reworded != PAGES["page_1"]
    assert ok.parse_register(reworded, 2026) is None
