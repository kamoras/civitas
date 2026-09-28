"""A day's Congressional Record granules: every page is listed, and a
failed listing is never an empty day."""

import asyncio

from app.pipeline.fetch import congressional_record as cr
from app.pipeline.fetch import house_record as hr


def _pages(pages):
    """Fake _fetch_json serving `pages` (list of dicts or None) in order."""
    seen = []

    async def fake(client, url):
        seen.append(url)
        return pages[len(seen) - 1]
    return fake, seen


def _g(i, cls):
    return {"granuleId": f"G{i}", "title": f"t{i}", "granuleClass": cls}


def test_every_page_is_listed_and_the_key_never_reaches_a_logged_url(monkeypatch):
    # CREC-2026-09-24: 274 granules, the House's first; one page of 100
    # held no Senate granule at all.
    first = {"granules": [_g(i, "HOUSE") for i in range(100)],
             "nextPage": "https://api.govinfo.gov/packages/X/granules?offsetMark=abc&pageSize=100&api_key=SECRET"}
    second = {"granules": [_g(100 + i, "SENATE") for i in range(77)], "nextPage": None}
    fake, seen = _pages([first, second])
    monkeypatch.setattr(cr, "_fetch_json", fake)
    listed = asyncio.run(cr.list_package_granules(None, "CREC-2026-09-24"))
    assert len(listed) == 177
    assert "api_key" not in seen[1] and "offsetMark=abc" in seen[1]


def test_senate_granules_on_a_house_first_day(db_session, monkeypatch):
    fake, _ = _pages([{"granules": [_g(i, "HOUSE") for i in range(100)], "nextPage": "https://x/p2"},
                      {"granules": [_g(200, "SENATE")], "nextPage": None}])
    monkeypatch.setattr(cr, "_fetch_json", fake)
    assert [g["granuleId"] for g in asyncio.run(cr.fetch_senate_granules(None, db_session, "CREC-X"))] == ["G200"]


def test_a_failed_listing_is_none_and_not_cached(db_session, monkeypatch):
    fake, seen = _pages([None, {"granules": [_g(1, "SENATE")], "nextPage": None}])
    monkeypatch.setattr(cr, "_fetch_json", fake)
    assert asyncio.run(cr.fetch_senate_granules(None, db_session, "CREC-Y")) is None
    # Not cached: the next call lists again and succeeds.
    assert len(asyncio.run(cr.fetch_senate_granules(None, db_session, "CREC-Y"))) == 1
    assert len(seen) == 2


def test_house_granules_use_the_same_listing(db_session, monkeypatch):
    fake, _ = _pages([{"granules": [_g(1, "HOUSE"), _g(2, "SENATE")], "nextPage": None}])
    monkeypatch.setattr(cr, "_fetch_json", fake)
    assert [g["granuleId"] for g in asyncio.run(hr.fetch_house_granules(None, db_session, "CREC-Z"))] == ["G1"]


def test_a_partial_run_is_not_cached(db_session, monkeypatch):
    async def packages(client, db, days_back):
        return ["CREC-2026-09-23", "CREC-2026-09-24"]

    async def granules(client, db, pkg):
        return None if pkg.endswith("24") else []

    monkeypatch.setattr(cr, "fetch_crec_packages", packages)
    monkeypatch.setattr(cr, "fetch_senate_granules", granules)
    assert asyncio.run(cr.fetch_floor_remarks(None, db_session)) == {}
    assert cr.api_cache_get(db_session, "govinfo", "floor-remarks-60d-v3") is None
