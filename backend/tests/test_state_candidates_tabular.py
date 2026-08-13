"""Tests for the shared bulk-results adapter (state_candidates_tabular.py).

The fixture is a real cut of North Carolina's official 2026-03-03 primary
export (fetched live 2026-08-12), keeping the multi-precinct rows that make
aggregation load-bearing, plus an "NC HOUSE OF REPRESENTATIVES DISTRICT
022" contest as a control — a STATE house race whose label looks federal at
a glance and must never be picked up as one.
"""

import io
import os
import zipfile

import pytest

from app.pipeline.fetch import state_candidates_tabular as tb

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures_nc_results_export.tsv")
_FORMAT = {
    "delimiter": "\t", "encoding": "utf-8",
    "contest_column": "Contest Name", "choice_column": "Choice",
    "party_column": "Choice Party", "votes_column": "Total Votes",
}


def _fixture_bytes() -> bytes:
    with open(_FIXTURE, "rb") as fh:
        return fh.read()


class TestVotes:
    def test_parses_thousands_separators(self):
        assert tb._votes("1,234") == 1234

    def test_non_numeric_contributes_nothing_rather_than_raising(self):
        assert tb._votes("n/a") == 0
        assert tb._votes("") == 0
        assert tb._votes(None) == 0


class TestRows:
    def test_reads_a_plain_delimited_export(self):
        rows = tb._rows(_fixture_bytes(), _FORMAT)
        assert len(rows) > 100
        assert "Contest Name" in rows[0]

    def test_transparently_unzips_a_single_member_archive(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("results_pct.txt", _fixture_bytes())
        rows = tb._rows(buf.getvalue(), _FORMAT)
        assert len(rows) > 100

    def test_ambiguous_multi_member_archive_is_refused(self):
        """Two candidate members means the configured shape no longer
        matches reality — parsing an arbitrary one would be a guess."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", _fixture_bytes())
            zf.writestr("b.txt", _fixture_bytes())
        assert tb._rows(buf.getvalue(), _FORMAT) is None

    def test_corrupt_zip_returns_none_rather_than_raising(self):
        assert tb._rows(b"PK\x03\x04garbage", _FORMAT) is None


class TestTally:
    def test_sums_a_candidate_across_every_precinct_row(self):
        """These exports are one row per precinct per choice — a single
        row's value is a fraction of the real total."""
        rows = tb._rows(_fixture_bytes(), _FORMAT)
        tally = tb._tally(rows, _FORMAT)
        foxx = tally["US HOUSE OF REPRESENTATIVES DISTRICT 05 (REP)"]["choices"]
        name = next(n for n in foxx if "Foxx" in n)
        single_row = max(
            tb._votes(r["Total Votes"]) for r in rows if r["Choice"] == name
        )
        assert foxx[name] > single_row


class TestDiscoverUrl:
    @pytest.mark.asyncio
    async def test_direct_url_substitutes_the_cycle_year(self):
        url = await tb._discover_url(
            None, "XX", 2026,
            {"mode": "direct_url", "url": "https://example.gov/{year}/results.csv"},
        )
        assert url == "https://example.gov/2026/results.csv"

    @pytest.mark.asyncio
    async def test_s3_listing_picks_the_earliest_matching_key(self, monkeypatch):
        """Within a cycle the first matching election is the primary that
        decides nominees; a later folder is the general or a second
        primary."""
        listing = (
            "<ListBucketResult>"
            "<Contents><Key>ENRS/2026_03_03/results_pct_20260303.zip</Key></Contents>"
            "<Contents><Key>ENRS/2026_11_03/results_pct_20261103.zip</Key></Contents>"
            "<Contents><Key>ENRS/2026_03_03/absentee_20260303.zip</Key></Contents>"
            "</ListBucketResult>"
        )

        async def fake_get(client, url, label):
            return _Resp(text=listing)

        monkeypatch.setattr(tb, "_get", fake_get)
        url = await tb._discover_url(None, "NC", 2026, {
            "mode": "s3_listing",
            "bucket_url": "https://s3.amazonaws.com/dl.ncsbe.gov",
            "prefix": "ENRS/{year}",
            "file_regex": r"results_pct_\d{8}\.zip",
        })
        assert url.endswith("ENRS/2026_03_03/results_pct_20260303.zip")

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_none(self):
        assert await tb._discover_url(None, "XX", 2026, {"mode": "carrier_pigeon"}) is None


class TestFetchConfirmedCandidates:
    @pytest.mark.asyncio
    async def test_returns_federal_nominees_and_excludes_the_state_house_control(
        self, monkeypatch,
    ):
        async def fake_discover(client, state, year, discovery):
            return "https://example.gov/results.zip"

        async def fake_get(client, url, label):
            return _Resp(content=_fixture_bytes())

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        records = await tb.fetch_confirmed_candidates(
            None, 2026, "NC", {"runoff_threshold_pct": 30.0, "format": _FORMAT},
        )

        assert {"office": "H", "district": 5, "party": "R", "last_name": "Foxx"} in records
        # "NC HOUSE OF REPRESENTATIVES DISTRICT 022" is a state race whose
        # label looks federal — district 22 must not appear.
        assert all(r["district"] != 22 for r in records)
        assert all(r["office"] in ("S", "H") for r in records)

    @pytest.mark.asyncio
    async def test_undiscoverable_file_returns_none_not_empty(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return None

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self, monkeypatch):
        async def fake_discover(client, state, year, discovery):
            return "https://example.gov/results.zip"

        async def fake_get(client, url, label):
            return None

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None

    @pytest.mark.asyncio
    async def test_oversized_download_is_refused(self, monkeypatch):
        """A URL that has silently started serving something enormous is a
        config failure, not something to parse."""
        async def fake_discover(client, state, year, discovery):
            return "https://example.gov/results.zip"

        async def fake_get(client, url, label):
            return _Resp(content=b"x" * (tb.MAX_DOWNLOAD_BYTES + 1))

        monkeypatch.setattr(tb, "_discover_url", fake_discover)
        monkeypatch.setattr(tb, "_get", fake_get)
        assert await tb.fetch_confirmed_candidates(None, 2026, "NC", {}) is None


class _Resp:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content
