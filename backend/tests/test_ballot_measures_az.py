"""Tests for Arizona's ballot-measure strategy (ballot_measures_az.py).

fixtures_az_publicity_pamphlet_2026.json is REAL: page_words() of the
Secretary of State's 2026 Publicity Pamphlet as the Citizens Clean
Elections Commission republishes it — the cover, the table of contents
and all eight Ballot Format pages (its _source names the URL).
fixtures_az_publicity_pamphlet_2026_es_cover.json is the Spanish
edition's cover. The pages the reader fetches around them are synthetic.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.pipeline.fetch import ballot_measure_pdf_sources as sources
from app.pipeline.fetch import ballot_measures_az as az
from app.pipeline.fetch import ballot_measures_pdf as pdf
from app.pipeline.fetch.ballot_measure_text import SourceBlocked

_FX = Path(__file__).parent
PAGES = json.loads((_FX / "fixtures_az_publicity_pamphlet_2026.json").read_text())["pages"]
ES_COVER = json.loads((_FX / "fixtures_az_publicity_pamphlet_2026_es_cover.json").read_text())["pages"]
CCEC = "https://www.azcleanelections.gov/voter-education-guide"
ENGLISH = "https://storageccec.blob.core.usgovcloudapi.net/public/docs/1533-2026AZPublicityPamphlet-English.pdf"
SPANISH = "https://storageccec.blob.core.usgovcloudapi.net/public/docs/1534-2026AZPublicityPamphletSpanish.pdf"


def _without(page, texts):
    return [w for w in page if w["text"] not in texts]


def test_every_proposition_in_the_table_of_contents_is_read():
    measures = az.parse_pamphlet(PAGES, 2026)
    assert [m["number"] for m in measures] == ["141", "142", "144", "316", "317", "318", "319", "320"]
    assert all(m["official_title"] is None and m["fiscal_impact"] is None for m in measures)
    assert {m["title_authority"] for m in measures} == {az.TITLE_AUTHORITY}


def test_ballot_format_is_quoted_without_the_ovals_or_the_side_tab():
    by_number = {m["number"]: m for m in az.parse_pamphlet(PAGES, 2026)}
    p141 = by_number["141"]
    assert p141["title"] == "PROPOSED AMENDMENT TO THE CONSTITUTION BY THE LEGISLATURE RELATING TO TAXATION"
    assert p141["official_summary"] == (
        "PROHIBITS TAXES OR FEES BASED ON VEHICLE MILES TRAVELED AND LAWS "
        "MONITORING OR LIMITING VEHICLE MILES TRAVELED WITHOUT CONSENT."
    )
    assert p141["yes_means"] == (
        "A “yes” vote shall have the effect of amending the Arizona Constitution to prohibit: (1) taxes or fees based on "
        "motor vehicle miles traveled; and (2) laws or rules that monitor or limit motor vehicle miles traveled "
        "without consent. These prohibitions do not apply in certain instances to interstate commercial "
        "vehicles or to vehicles owned by the state or local governments."
    )
    assert p141["no_means"] == "A “no” vote shall have the effect of maintaining the current constitutional language related to taxation."
    # A right-hand page whose tab number sits beside the NO statement.
    assert by_number["142"]["no_means"].endswith("based on race, ethnicity, or other classes.")
    # A left-hand page, whose designation line starts left of the body.
    assert by_number["318"]["title"] == "REFERRED TO THE PEOPLE BY THE LEGISLATURE RELATING TO STUDENT ATHLETICS"
    for m in by_number.values():
        for field in ("title", "official_summary", "yes_means", "no_means"):
            assert "YES" not in m[field].split() and "NO" not in m[field].split(), (m["number"], field)
            assert not m[field].split()[-1].isdigit(), (m["number"], field)  # no page or tab number


def test_the_cover_must_name_this_years_general():
    assert az.is_publicity_pamphlet(PAGES, 2026)
    assert az.parse_pamphlet(PAGES, 2028) is None
    assert not az.is_publicity_pamphlet(ES_COVER, 2026)
    assert not az.looks_like_pamphlet(ES_COVER, 2026)


def test_a_ballot_format_page_missing_from_the_contents_refuses():
    assert az.parse_pamphlet([p for i, p in enumerate(PAGES) if i != 5], 2026) is None


def test_a_page_missing_a_vote_statement_refuses():
    pages = list(PAGES)
    pages[3] = [w for w in pages[3] if w["text"] != "“no”"]
    assert az.parse_pamphlet(pages, 2026) is None


def test_a_challenge_or_unfetchable_state_page_is_a_blocked_source(monkeypatch):
    async def challenge(client, url, label, **kw):
        return "<html><head><title>Just a moment...</title></head></html>"

    monkeypatch.setattr(az, "get_text", challenge)
    with pytest.raises(SourceBlocked):
        asyncio.run(az.fetch_measures(None, 2026))

    async def nothing(client, url, label, **kw):
        # A challenge is asked once: retrying carries the cookie the
        # challenge set, which is getting past it.
        assert kw == {"retry_on_4xx": False}
        return None

    monkeypatch.setattr(az, "get_text", nothing)
    with pytest.raises(SourceBlocked):
        asyncio.run(az.fetch_measures(None, 2026))


def test_the_state_pages_pamphlet_links_are_found():
    page = (
        "<html><head><title>Ballot Measures | Arizona Secretary of State</title></head><body>"
        "<a href='/sites/default/files/docs/2026-publicity-pamphlet-english.pdf'>2026 Publicity Pamphlet (PDF)</a>"
        "<a href='/sites/default/files/docs/2024-publicity-pamphlet.pdf'>2024 Publicity Pamphlet</a>"
        "<a href='/other.pdf'>Something else</a></body></html>"
    )
    assert az.pamphlet_urls(page, 2026) == ["https://azsos.gov/sites/default/files/docs/2026-publicity-pamphlet-english.pdf"]


def _republished(monkeypatch, docs):
    async def candidates(client, page, year, words):
        assert (page, year, words) == (CCEC, 2026, ("publicity pamphlet",))
        return list(docs)

    async def get_bytes(client, url, label, **kw):
        return url

    monkeypatch.setattr(az, "republished_candidates", candidates)
    monkeypatch.setattr(az, "get_bytes", get_bytes)
    monkeypatch.setattr(az, "page_words", lambda raw: docs[raw])


@pytest.mark.asyncio
async def test_the_pamphlet_is_read_from_the_republished_copy_with_every_check(monkeypatch):
    _republished(monkeypatch, {ENGLISH: PAGES, SPANISH: ES_COVER})
    result = await az.fetch_republished(None, 2026, CCEC)
    assert len(result) == 8 and {url for _, url in result} == {ENGLISH}


@pytest.mark.asyncio
async def test_a_copy_without_the_verified_cover_is_refused(monkeypatch):
    reworded = [_without(PAGES[0], {"PUBLICITY"})] + PAGES[1:]
    reworded[0].append({"text": "Publicity", "x0": 300.0, "top": 90.0, "upright": True})
    reworded[0].append({"text": "Pamphlet", "x0": 360.0, "top": 90.0, "upright": True})
    _republished(monkeypatch, {ENGLISH: reworded})
    assert await az.fetch_republished(None, 2026, CCEC) is None


@pytest.mark.asyncio
async def test_a_page_without_the_pamphlet_is_no_answer(monkeypatch):
    _republished(monkeypatch, {SPANISH: ES_COVER})
    assert await az.fetch_republished(None, 2026, CCEC) is None


@pytest.mark.asyncio
async def test_blocked_arizona_falls_back_to_the_named_copy_and_says_so(monkeypatch, db_session):
    async def blocked(client, year):
        raise SourceBlocked("challenge")

    _republished(monkeypatch, {ENGLISH: PAGES})
    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "az_publicity_pamphlet", blocked)
    sources.invalidate_cache()
    measures = await pdf.fetch_state_measures_pdf(None, db_session, "AZ", 2026, "2026-11-03")
    assert len(measures) == 8
    assert {m["republished_by"] for m in measures} == {"Arizona Citizens Clean Elections Commission"}
    assert measures[0]["id"] == "AZ-2026-11-03-141"
