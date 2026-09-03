"""Tests for the statewide-canvass-XML-over-FTP strategy
(state_candidates_canvass_xml.py). Fixture is a real, trimmed export
(fixtures_az_canvass_summary.xml) fetched live from ftp.azsos.gov —
Arizona's 2026 primary, District 1's Democratic and Republican
contests (each a real multi-candidate field) plus District 3's Green
contest, which is a write-in-only race with no real nominee."""

import os

import pytest

from app.pipeline.fetch import state_candidates_canvass_xml as cx

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures_az_canvass_summary.xml")
_SOURCE = {
    "discovery": {
        "base_url": "ftp://ftp.example.gov/ElectionResults/{year}/State/",
        "keyword": "Primary Election",
        "settle_days": 14,
    },
}


def _raw() -> bytes:
    with open(_FIXTURE, "rb") as fh:
        return fh.read()


@pytest.mark.asyncio
class TestFetchConfirmedCandidates:
    async def test_real_fixture_names_the_real_top_votegetter(self, monkeypatch):
        monkeypatch.setattr(cx, "_ftp_list", lambda url: ["2026 Primary Election"])
        monkeypatch.setattr(cx, "_ftp_get", lambda url: _raw())

        results = await cx.fetch_confirmed_candidates(None, 2026, "AZ", _SOURCE)

        # District 1 Democratic: Shah led Galán-Woods 29002-25267 — the
        # plurality winner, not the alphabetically- or first-listed one.
        assert {"office": "H", "district": 1, "party": "D", "last_name": "Shah"} in results
        assert {"office": "H", "district": 1, "party": "R", "last_name": "Feely"} in results
        # District 3 Green: the only choice is a write-in (isWriteIn=true),
        # so this contest names nobody — a real field with zero real
        # candidates is not the same as a contest that failed to parse.
        assert not any(r["district"] == 3 for r in results)
        assert len(results) == 2

    async def test_no_matching_folder_yet_returns_none(self, monkeypatch):
        # The year directory exists (base_url already scopes by {year}) but
        # nothing matching "Primary Election" has been posted into it yet.
        monkeypatch.setattr(cx, "_ftp_list", lambda url: [])
        monkeypatch.setattr(cx, "_ftp_get", lambda url: pytest.fail("should not fetch"))

        assert await cx.fetch_confirmed_candidates(None, 2026, "AZ", _SOURCE) is None

    async def test_directory_listing_failure_returns_none(self, monkeypatch):
        def raise_it(url):
            raise OSError("connection refused")

        monkeypatch.setattr(cx, "_ftp_list", raise_it)

        assert await cx.fetch_confirmed_candidates(None, 2026, "AZ", _SOURCE) is None

    async def test_file_fetch_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(cx, "_ftp_list", lambda url: ["2026 Primary Election"])
        monkeypatch.setattr(cx, "_ftp_get", lambda url: None)

        assert await cx.fetch_confirmed_candidates(None, 2026, "AZ", _SOURCE) is None

    async def test_malformed_xml_returns_none(self, monkeypatch):
        monkeypatch.setattr(cx, "_ftp_list", lambda url: ["2026 Primary Election"])
        monkeypatch.setattr(cx, "_ftp_get", lambda url: b"<not><valid")

        assert await cx.fetch_confirmed_candidates(None, 2026, "AZ", _SOURCE) is None

    async def test_incomplete_count_withholds_as_empty_not_none(self, monkeypatch):
        # Real jurisdiction element, but reporting isn't 100% yet — the
        # count is still running, so this must not claim any nominee
        # (empty, "confirms nobody"), and must not read as a fetch
        # failure either (None, "couldn't check").
        partial = _raw().replace(b'precinctsReportingPercent="100.00"', b'precinctsReportingPercent="87.00"', 1)
        monkeypatch.setattr(cx, "_ftp_list", lambda url: ["2026 Primary Election"])
        monkeypatch.setattr(cx, "_ftp_get", lambda url: partial)

        assert await cx.fetch_confirmed_candidates(None, 2026, "AZ", _SOURCE) == []

    async def test_missing_discovery_config_returns_none(self, monkeypatch):
        assert await cx.fetch_confirmed_candidates(None, 2026, "AZ", {}) is None


@pytest.mark.asyncio
class TestDiscoverPrimaryDate:
    async def test_reads_the_document_s_own_election_date(self, monkeypatch):
        monkeypatch.setattr(cx, "_ftp_list", lambda url: ["2026 Primary Election"])
        monkeypatch.setattr(cx, "_ftp_get", lambda url: _raw())

        assert await cx.discover_primary_date(None, 2026, "AZ", _SOURCE) == {"primary": "2026-07-21"}

    async def test_no_document_yet_returns_empty(self, monkeypatch):
        monkeypatch.setattr(cx, "_ftp_list", lambda url: [])

        assert await cx.discover_primary_date(None, 2026, "AZ", _SOURCE) == {}
